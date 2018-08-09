from collections import OrderedDict

import numpy as np
import xarray as xr


class QFDataArray(xr.DataArray):
    DATES = "dates"
    TICKERS = "tickers"
    FIELDS = "fields"

    def __init__(self, data, coords=None, dims=None, name=None,
                 attrs=None, encoding=None, fastpath=False):
        """
        Use the class method `create()` for creating QFDataArrays.
        DON"T CREATE QFDataArrays using __init__() method (don't create it like this: QFDataArray()).
        The __init__ method should be used only by xr.DataArray internal methods.
        """
        if not fastpath:
            self._check_if_dimensions_are_correct(coords, dims)

        super().__init__(data, coords, dims, name, attrs, encoding, fastpath)

    def __setattr__(self, name, value):
        # Makes it possible to set indices in this way: qf_data_array.fields = ["OPEN", "CLOSE"].
        # Otherwise one would need to set them like this: qf_data_array[QFDataArray.FIELDS] = ["OPEN", "CLOSE"]
        # if name == self.TICKERS or name == self.DATES or name == self.FIELDS:
        if name in [self.FIELDS, self.TICKERS, self.DATES]:
            self.__setitem__(name, value)
        else:
            super().__setattr__(name, value)

    @classmethod
    def create(cls, dates, tickers, fields, data=None, name=None):
        """
        Helper method for creating a QFDataArray. __init__() methods can't be used for that, because its signature
        must be the same as the signature of xr.DataArray.__init__().
        
        Example:
        a = QFDataArray.create(
            dates=pd.date_range('2017-01-01', periods=3),
            tickers=['a', 'b'],
            fields=['field'],
            data=[
                 [[1.0], [2.0]],
                 [[3.0], [4.0]],
                 [[5.0], [6.0]]
            ])

        Parameters
        ----------
        data
            data that should be put in the array (it's dimensions must be in the proper order: dates, tickers, fields).
        dates
            dates index (labels)
        tickers
            tickers index (labels)
        fields
            fields index (labels)
        name
            name of the QFDataArray

        Returns
        -------
        QFDataArray
        """
        coordinates = {cls.DATES: dates, cls.TICKERS: tickers, cls.FIELDS: fields}
        dimensions = (cls.DATES, cls.TICKERS, cls.FIELDS)

        # if no data is provided, the empty array will be created
        if data is None:
            data = np.empty((len(dates), len(tickers), len(fields)))
            data[:] = np.nan

        return QFDataArray(data, coordinates, dimensions, name)

    @classmethod
    def from_xr_data_array(cls, xr_data_array: xr.DataArray):
        """
        Converts regular xr.DataArray into QFDataArray.

        Parameters
        ----------
        xr_data_array
            xr.DataArray with 3 dimensions: dates, tickers and fields.

        Returns
        -------
        QFDataArray
        """
        xr_data_array = xr_data_array.transpose(cls.DATES, cls.TICKERS, cls.FIELDS)
        qf_data_array = QFDataArray.create(xr_data_array.dates, xr_data_array.tickers, xr_data_array.fields,
                                           xr_data_array.data, xr_data_array.name)
        return qf_data_array

    @classmethod
    def concat(cls, objs, dim=None, data_vars='all', coords='different', compat='equals', positions=None,
               indexers=None, mode=None, concat_over=None):
        """
        Concatenates different xr.DataArrays and then converts the result to QFDataArray.

        See Also
        --------
        docstring for xr.concat()
        """
        result = xr.concat(
            objs, dim, data_vars, coords, compat,positions, indexers, mode, concat_over
        )  # type: xr.DataArray
        result = QFDataArray.from_xr_data_array(result)

        return result

    def _check_if_dimensions_are_correct(self, coords, dims):
        expected_dimensions = (self.DATES, self.TICKERS, self.FIELDS)
        if dims is not None:
            actual_dimensions = tuple(dims)
        elif coords is not None and isinstance(coords, OrderedDict):
            actual_dimensions = tuple(coords.keys())
        else:
            actual_dimensions = None
        if actual_dimensions != expected_dimensions:
            raise ValueError("Dimensions must be equal to: {}".format(expected_dimensions))