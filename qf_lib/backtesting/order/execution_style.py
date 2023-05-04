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


class ExecutionStyle:

    def __str__(self):
        return self.__class__.__name__


class MarketOrder(ExecutionStyle):
    def __eq__(self, other):
        return type(other) == MarketOrder

    def __hash__(self):
        return hash(self.__class__.__name__)


class MarketOnCloseOrder(ExecutionStyle):
    def __eq__(self, other):
        return type(other) == MarketOnCloseOrder

    def __hash__(self):
        return hash(self.__class__.__name__)


class StopOrder(ExecutionStyle):
    def __init__(self, stop_price: float):
        self.stop_price = stop_price

    def __str__(self):
        return f"{self.__class__.__name__} - stop price: {self.stop_price}"

    def __eq__(self, other):
        if other is self:
            return True

        return (
            self.stop_price == other.stop_price
            if isinstance(other, StopOrder)
            else False
        )

    def __hash__(self):
        return hash((self.__class__.__name__, self.stop_price))
