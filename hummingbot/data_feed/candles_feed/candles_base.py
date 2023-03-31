import asyncio
from collections import deque
from typing import Optional

import pandas as pd

from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.network_base import NetworkBase
from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant


class CandlesBase(NetworkBase):
    """
    This class serves as a base class for fetching and storing candle data from a cryptocurrency exchange.
    The class uses the Rest and WS Assistants for all the IO operations, and a double-ended queue to store candles.
    Also implements the Throttler module for API rate limiting, but it's not so necessary since the realtime data should
    be updated via websockets mainly.
    """
    columns = ["timestamp", "open", "high", "low", "close", "volume", "quote_asset_volume",
               "n_trades", "taker_buy_base_volume", "taker_buy_quote_volume"]

    def __init__(self, trading_pair: str, interval: str = "1m", max_records: int = 150):
        super().__init__()
        async_throttler = AsyncThrottler(rate_limits=self.rate_limits)
        self._api_factory = WebAssistantsFactory(throttler=async_throttler)
        self._candles = deque(maxlen=max_records)
        self._listen_candles_task: Optional[asyncio.Task] = None
        self._trading_pair = trading_pair
        self._ex_trading_pair = self.get_exchange_trading_pair(trading_pair)
        if interval in self.intervals.keys():
            self.interval = interval
        else:
            self.logger().exception(f"Interval {interval} is not supported. Available Intervals: {self.intervals.keys()}")
            raise

    async def start_network(self):
        """
        This method starts the network and starts a task for listen_for_subscriptions.
        """
        await self.stop_network()
        self._listen_candles_task = safe_ensure_future(self.listen_for_subscriptions())

    async def stop_network(self):
        """
        This method stops the network by canceling the _listen_candles_task task.
        """
        if self._listen_candles_task is not None:
            self._listen_candles_task.cancel()
            self._listen_candles_task = None

    @property
    def is_ready(self):
        """
        This property returns a boolean indicating whether the _candles deque has reached its maximum length.
        """
        return len(self._candles) == self._candles.maxlen

    @property
    def name(self):
        raise NotImplementedError

    @property
    def rest_url(self):
        raise NotImplementedError

    @property
    def health_check_url(self):
        raise NotImplementedError

    @property
    def candles_url(self):
        raise NotImplementedError

    @property
    def wss_url(self):
        raise NotImplementedError

    @property
    def rate_limits(self):
        raise NotImplementedError

    @property
    def intervals(self):
        raise NotImplementedError

    async def check_network(self) -> NetworkStatus:
        raise NotImplementedError

    @property
    def candles_df(self) -> pd.DataFrame:
        """
        This property returns the candles stored in the _candles deque as a Pandas DataFrame.
        """
        return pd.DataFrame(self._candles, columns=self.columns, dtype=float)

    def get_exchange_trading_pair(self, trading_pair):
        raise NotImplementedError

    async def fetch_candles(self,
                            start_time: Optional[int] = None,
                            end_time: Optional[int] = None,
                            limit: Optional[int] = 500):
        """
        This is an abstract method that must be implemented by a subclass to fetch candles from the exchange API.
        :param start_time: start time to fetch candles
        :param end_time: end time to fetch candles
        :param limit: quantity of candles
        :return: numpy array with the candlesticks
        """
        raise NotImplementedError

    async def fill_historical_candles(self):
        """
        This is an abstract method that must be implemented by a subclass to fill the _candles deque with historical candles.
        """
        raise NotImplementedError

    async def listen_for_subscriptions(self):
        """
        Connects to the candlestick websocket endpoint and listens to the messages sent by the
        exchange.
        """
        ws: Optional[WSAssistant] = None
        while True:
            try:
                ws: WSAssistant = await self._connected_websocket_assistant()
                await self._subscribe_channels(ws)
                await self._process_websocket_messages(websocket_assistant=ws)
            except asyncio.CancelledError:
                raise
            except ConnectionError as connection_exception:
                self.logger().warning(f"The websocket connection was closed ({connection_exception})")
            except Exception:
                self.logger().exception(
                    "Unexpected error occurred when listening to public klines. Retrying in 1 seconds...",
                )
                await self._sleep(1.0)
            finally:
                await self._on_order_stream_interruption(websocket_assistant=ws)

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        await ws.connect(ws_url=self.wss_url,
                         ping_timeout=30)
        return ws

    async def _subscribe_channels(self, ws: WSAssistant):
        """
        Subscribes to the candles events through the provided websocket connection.
        :param ws: the websocket assistant used to connect to the exchange
        """
        raise NotImplementedError

    async def _process_websocket_messages(self, websocket_assistant: WSAssistant):
        raise NotImplementedError

    async def _sleep(self, delay):
        """
        Function added only to facilitate patching the sleep in unit tests without affecting the asyncio module
        """
        await asyncio.sleep(delay)

    async def _on_order_stream_interruption(self, websocket_assistant: Optional[WSAssistant] = None):
        websocket_assistant and await websocket_assistant.disconnect()
        self._candles.clear()
