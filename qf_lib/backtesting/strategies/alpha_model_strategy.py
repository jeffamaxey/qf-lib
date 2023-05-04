#     Copyright 2016-present CERN – European Organization for Nuclear Research
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.

import random
from typing import List, Dict, Sequence, Optional, Union

import numpy as np

from qf_lib.backtesting.alpha_model.alpha_model import AlphaModel
from qf_lib.backtesting.alpha_model.exposure_enum import Exposure
from qf_lib.backtesting.order.time_in_force import TimeInForce
from qf_lib.backtesting.signals.signal import Signal
from qf_lib.backtesting.broker.broker import Broker
from qf_lib.backtesting.order.order_factory import OrderFactory
from qf_lib.backtesting.portfolio.position import Position
from qf_lib.backtesting.strategies.abstract_strategy import AbstractStrategy
from qf_lib.backtesting.trading_session.trading_session import TradingSession
from qf_lib.common.exceptions.future_contracts_exceptions import NoValidTickerException
from qf_lib.common.tickers.tickers import Ticker
from qf_lib.common.utils.dateutils.timer import Timer
from qf_lib.common.utils.logging.qf_parent_logger import qf_logger
from qf_lib.containers.futures.future_tickers.future_ticker import FutureTicker
from qf_lib.containers.futures.futures_rolling_orders_generator import FuturesRollingOrdersGenerator
from qf_lib.data_providers.data_provider import DataProvider


class AlphaModelStrategy(AbstractStrategy):
    """
    Puts together models and all settings around it and generates orders on before market open.

    Parameters
    ----------
    ts: TradingSession
        Trading session
    model_tickers_dict: Dict[AlphaModel, Sequence[Ticker]]
        Dict mapping models to list of tickers that the model trades. (The tickers for which the
        model gives recommendations)
    use_stop_losses: bool
        flag indicating if the stop losses should be used or not. If False, all stop orders are ignored. By default, the
        value is set to True.
    max_open_positions: Optional[int]
        maximal number of positions that may be open at the same time in the portfolio. If the value is set to None,
        the number of maximal open positions is not limited. By default this value is set to None.
    time_in_force: Optional[TimeInForce]
        time in force for the orders that will be generated by the AlphaModelStrategy (length of time over which the
        generated orders will continue working before they are canceled). By default, the OPG time in force is used.
    """

    def __init__(self, ts: TradingSession, model_tickers_dict: Dict[AlphaModel, Sequence[Ticker]], use_stop_losses=True,
                 max_open_positions: Optional[int] = None, time_in_force: Optional[TimeInForce] = TimeInForce.OPG):
        super().__init__(ts)

        all_future_tickers = [ticker for tickers_for_model in model_tickers_dict.values()
                              for ticker in tickers_for_model if isinstance(ticker, FutureTicker)]

        self._futures_rolling_orders_generator = self._get_futures_rolling_orders_generator(all_future_tickers,
                                                                                            ts.timer, ts.data_provider,
                                                                                            ts.broker, ts.order_factory)
        self._broker = ts.broker
        self._order_factory = ts.order_factory
        self._position_sizer = ts.position_sizer
        self._orders_filters = ts.orders_filters
        self._frequency = ts.frequency

        assert ts.frequency is not None, "Trading Session does not have the frequency parameter set. You need to set " \
                                         "it before using the Alpha Model Strategy."

        self._model_tickers_dict = model_tickers_dict
        self._use_stop_losses = use_stop_losses
        self._max_open_positions = max_open_positions
        self._time_in_force = time_in_force

        self.logger = qf_logger.getChild(self.__class__.__name__)
        self._log_configuration()

    def _get_futures_rolling_orders_generator(self, future_tickers: Sequence[FutureTicker], timer: Timer,
                                              data_provider: DataProvider, broker: Broker, order_factory: OrderFactory):
        # Initialize timer and data provider in case of FutureTickers
        for future_ticker in future_tickers:
            future_ticker.initialize_data_provider(timer, data_provider)

        return FuturesRollingOrdersGenerator(future_tickers, timer, broker, order_factory)

    def calculate_and_place_orders(self):
        date = self.timer.now().date()
        self.logger.info(f"[{date}] Signals Generation Started")
        signals = self._calculate_signals()
        self.logger.info(f"[{date}] Signals Generation Finished")

        self.logger.debug("Signals: ")
        for s in signals:
            self.logger.debug(str(s))

        if self._max_open_positions is not None:
            self._adjust_number_of_open_positions(signals)

        self.logger.info(f"[{date}] Placing Orders")
        self._place_orders(signals)
        self.logger.info(f"[{date}] Orders Placed")

    def _adjust_number_of_open_positions(self, signals: List[Signal]):
        """
        Adjust the number of positions that, after placing the orders, will be open in th portfolio, so that it
        will not exceed the maximum number.

        In case if we already reached the maximum number of positions in the portfolio and we get 2 new signals,
        one for opening and one for closing a position, we ignore the opening position signal in case if during
        position closing an error would occur and the position will remain in the portfolio.

        Regarding Futures Contracts:
        While checking the number of all possible open positions we consider family of contracts
        (for example Gold) and not specific contracts (Jul 2020 Gold). Therefore even if 2 or more contracts
        corresponding to one family existed in the portfolio, they would be counted as 1 open position.
        """
        open_positions_specific_tickers = {
            position.ticker() for position in self._broker.get_positions()
        }

        def position_for_ticker_exists_in_portfolio(ticker: Ticker) -> bool:
            if isinstance(ticker, FutureTicker):
                # Check if any of specific tickers with open positions in portfolio belongs to tickers family
                return any(
                    ticker.belongs_to_family(t)
                    for t in open_positions_specific_tickers
                )
            else:
                return ticker in open_positions_specific_tickers

        # Signals corresponding to tickers, that already have a position open in the portfolio
        open_positions_signals = [s for s in signals if position_for_ticker_exists_in_portfolio(s.ticker)]

        # Signals, which indicate openings of new positions in the portfolio
        new_positions_signals = [s for s in signals if not position_for_ticker_exists_in_portfolio(s.ticker) and
                                 s.suggested_exposure != Exposure.OUT]

        number_of_positions_to_be_open = len(new_positions_signals) + len(open_positions_signals)

        if number_of_positions_to_be_open > self._max_open_positions:
            self.logger.info("The number of positions to be open exceeds the maximum limit of {}. Some of the signals "
                             "need to be changed.".format(self._max_open_positions))

            no_of_signals_to_change = number_of_positions_to_be_open - self._max_open_positions

            # Select a random subset of signals, for which the exposure will be set to OUT (in order not to exceed the
            # maximum), which would be deterministic across multiple backtests
            random.seed(self.timer.now().timestamp())
            new_positions_signals = sorted(new_positions_signals, key=lambda s: s.fraction_at_risk)
            signals_to_change = random.sample(new_positions_signals, no_of_signals_to_change)

            for signal in signals_to_change:
                signal.suggested_exposure = Exposure.OUT

        return signals

    def _calculate_signals(self):
        current_positions = self._broker.get_positions()
        signals = []

        for model, tickers in self._model_tickers_dict.items():
            for ticker in set(tickers):
                try:
                    current_exposure = self._get_current_exposure(ticker, current_positions)
                    signal = model.get_signal(ticker, current_exposure, self.timer.now(), self._frequency)
                    signals.append(signal)
                except NoValidTickerException:
                    pass

        return signals

    def _place_orders(self, signals):
        self.logger.info(
            f"Converting Signals to Orders using: {self._position_sizer.__class__.__name__}"
        )
        orders = self._position_sizer.size_signals(signals, self._use_stop_losses, self._time_in_force, self._frequency)

        close_orders = self._futures_rolling_orders_generator.generate_close_orders()
        orders = orders + close_orders

        for orders_filter in self._orders_filters:
            if orders:
                self.logger.info(
                    f"Filtering Orders based on selected requirements: {orders_filter}"
                )
                orders = orders_filter.adjust_orders(orders)

        self.logger.info("Cancelling all open orders")
        self._broker.cancel_all_open_orders()

        self.logger.info("Placing orders")
        self._broker.place_orders(orders)

    def _get_current_exposure(self, ticker: Union[Ticker, FutureTicker], current_positions: List[Position]) -> Exposure:
        """
        Returns current exposure of the given ticker in the portfolio. Alpha model strategy assumes there should be only
        one position per ticker in the portfolio.

        In case of future tickers this may not always be true - e.g. in case if a certain future contract expires and
        the rolling occurs we may end up with two positions open, when the old contract could not have been sold at the
        initially desired time. This situation usually does not happen often nor last too long, as the strategy will try
        to close the remaining position as soon as possible. Because of that, the current exposure of the ticker is
        defined either as the exposure of current contract or (if current contract is not present in the portfolio)
        the exposure of the previous contract.
        """
        ticker_to_quantity = {position.ticker(): position.quantity() for position in current_positions}
        assert len(ticker_to_quantity.keys()) == len(current_positions), "There should be max 1 position open per" \
                                                                             " ticker"

        current_ticker = ticker.get_current_specific_ticker() if isinstance(ticker, FutureTicker) else ticker
        current_ticker_quantity = ticker_to_quantity.get(current_ticker, 0)

        # There are no positions open for the current (specific) contract, in case of Future Tickers it is possible that
        # there are still positions open for previous contracts - in that case exposure will be based on them
        if current_ticker_quantity == 0 and isinstance(ticker, FutureTicker):
            matching_positions = [p for p in current_positions if ticker.belongs_to_family(p.ticker())]

            if len(matching_positions) > 1:
                matching_tickers = [p.ticker().as_string() for p in matching_positions]
                raise AssertionError(
                    f'There should be no more then 1 position open for an already expired contract for a future ticker. Detected positions open for the following contracts: {", ".join(matching_tickers)}.'
                )

            current_ticker_quantity = (
                matching_positions[0].quantity() if matching_positions else 0
            )

        return Exposure(np.sign(current_ticker_quantity))

    def _log_configuration(self):
        self.logger.info("AlphaModelStrategy configuration:")
        for model, tickers in self._model_tickers_dict.items():
            self.logger.info(f'Model: {str(model)}')
            for ticker in tickers:
                try:
                    self.logger.info(f'\t Ticker: {ticker.name}')
                except NoValidTickerException as e:
                    self.logger.info(e)
