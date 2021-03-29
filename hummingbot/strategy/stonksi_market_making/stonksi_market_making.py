from decimal import Decimal
import logging
import asyncio
import random
from typing import Dict, List, Set
from math import floor, ceil
import pandas as pd
import numpy as np
from statistics import mean
import time
from hummingbot.core.clock import Clock
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy.strategy_py_base import StrategyPyBase
from hummingbot.connector.exchange_base import ExchangeBase
from hummingbot.strategy.market_trading_pair_tuple import MarketTradingPairTuple
from .data_types import Proposal, PriceSize
from hummingbot.core.event.events import OrderType, TradeType
from hummingbot.core.data_type.limit_order import LimitOrder
from hummingbot.core.utils.estimate_fee import estimate_fee
from hummingbot.core.utils.market_price import usd_value
from hummingbot.strategy.pure_market_making.inventory_skew_calculator import (
    calculate_bid_ask_ratios_from_base_asset_ratio
)
#from hummingbot.connector.parrot import get_campaign_summary
NaN = float("nan")
s_decimal_zero = Decimal(0)
s_decimal_nan = Decimal("NaN")
lms_logger = None


class StonksiMarketMakingStrategy(StrategyPyBase):

    @classmethod
    def logger(cls) -> HummingbotLogger:
        global lms_logger
        if lms_logger is None:
            lms_logger = logging.getLogger(__name__)
        return lms_logger

    def __init__(self,
                 exchange: ExchangeBase,
                 market_infos: Dict[str, MarketTradingPairTuple],
                 token: str,
                 order_amount: Decimal,
                 spread: Decimal,
                 inventory_skew_enabled: bool,
                 inventory_target_base_pct: Decimal,
                 inventory_range_multiplier: Decimal = Decimal("1"),
                 order_refresh_time: float = 10.,
                 order_refresh_tolerance_pct: Decimal = Decimal("0.2"),
                 #volatility_interval: int = 60 * 5,
                 #avg_volatility_period: int = 10,
                 #volatility_to_spread_multiplier: Decimal = Decimal("1"),
                 max_spread: Decimal = Decimal("-1"),
                 max_order_age: float = 60. * 60.,
                 order_optimization_enabled: bool = False,
                 order_optimization_depth_pct: Decimal = Decimal("0"),
                 order_optimization_failsafe_enabled: bool = True,
                 inventory_max_available_token_amount: Decimal = Decimal("-1"),
                 status_report_interval: float = 900,
                 hb_app_notification: bool = False):
        super().__init__()
        self._exchange = exchange
        self._market_infos = market_infos
        self._token = token
        self._order_amount = order_amount
        self._spread = spread
        self._order_refresh_time = order_refresh_time
        self._order_refresh_tolerance_pct = order_refresh_tolerance_pct
        self._inventory_skew_enabled = inventory_skew_enabled
        self._inventory_target_base_pct = inventory_target_base_pct
        self._inventory_range_multiplier = inventory_range_multiplier
        #self._volatility_interval = volatility_interval
        #self._avg_volatility_period = avg_volatility_period
        #self._volatility_to_spread_multiplier = volatility_to_spread_multiplier
        self._max_spread = max_spread
        self._max_order_age = max_order_age
        self._order_optimization_enabled = order_optimization_enabled
        self._order_optimization_depth_pct = order_optimization_depth_pct
        self._order_optimization_failsafe_enabled = order_optimization_failsafe_enabled
        self._inventory_max_available_token_amount = inventory_max_available_token_amount
        self._ev_loop = asyncio.get_event_loop()
        self._last_timestamp = 0
        self._status_report_interval = status_report_interval
        self._ready_to_trade = False
        self._refresh_times = {market: 0 for market in market_infos}
        self._token_balances = {}
        self._sell_budgets = {}
        self._buy_budgets = {}
        #self._mid_prices = {market: [] for market in market_infos}
        #self._volatility = {market: s_decimal_nan for market in self._market_infos}
        self._last_vol_reported = 0.
        self._hb_app_notification = hb_app_notification
        #self._order_overhaul_countdown = random.uniform(7.0, 13.0)
        self._trading_pairs_to_redo = []

        self.add_markets([exchange])

    @property
    def active_orders(self):
        limit_orders = self.order_tracker.active_limit_orders
        return [o[1] for o in limit_orders]

    def tick(self, timestamp: float):
        """
        Clock tick entry point, is run every second (on normal tick setting).
        :param timestamp: current tick timestamp
        """
        if not self._ready_to_trade:
            # Check if there are restored orders, they should be canceled before strategy starts.
            self._ready_to_trade = self._exchange.ready and len(self._exchange.limit_orders) == 0
            if not self._exchange.ready:
                self.logger().warning(f"{self._exchange.name} is not ready. Please wait...")
                return
            else:
                self.logger().info(f"{self._exchange.name} is ready. Trading started.")
                self.create_budget_allocation()
                self._ready_to_trade = True

        time.sleep(random.uniform(0.0, 0.2))
        #self.update_mid_prices()
        #self.update_volatility()
        proposals = self.create_base_proposals()
        self._token_balances = self.adjusted_available_balances()
        if self._order_optimization_enabled:
            self.apply_order_optimization(proposals)
        if self._inventory_skew_enabled:
            self.apply_inventory_skew(proposals)
        self.apply_budget_constraint(proposals)
        self.cancel_active_orders(proposals)
        self.execute_orders_proposal(proposals)

        self._last_timestamp = timestamp

        #if (self._order_overhaul_countdown < 1):
        #    self._order_overhaul_countdown = random.uniform(100.0, 140.0)
        #    open_orders = safe_gather(self._exchange.get_open_orders())
        #    for cl_order_id, tracked_order in self._exchange.in_flight_orders.items():
        #        open_order = [o for o in open_orders if o.client_order_id == cl_order_id]
        #        if not open_order:
        #            self._exchange.trigger_event(MarketEvent.OrderCancelled,
        #                               OrderCancelledEvent(self.current_timestamp, cl_order_id))
        #            self._exchange.stop_tracking_order(cl_order_id)
        #else:
        #    self._order_overhaul_countdown -= 1

    @staticmethod
    def order_age(order: LimitOrder) -> float:
        if "//" not in order.client_order_id:
            return int(time.time()) - int(order.client_order_id[-16:]) / 1e6
        return -1.

    async def active_orders_df(self) -> pd.DataFrame:
        size_q_col = f"Amt({self._token})" if self.is_token_a_quote_token() else "Amt(Quote)"
        columns = ["Market", "Side", "Price", "Spread", "Amount", size_q_col, "Age"]
        data = []
        for order in self.active_orders:
            mid_price = self._market_infos[order.trading_pair].get_mid_price()
            spread = 0 if mid_price == 0 else abs(order.price - mid_price) / mid_price
            size_q = order.quantity * mid_price
            age = self.order_age(order)
            # // indicates order is a paper order so 'n/a'. For real orders, calculate age.
            age_txt = "n/a" if age <= 0. else pd.Timestamp(age, unit='s').strftime('%H:%M:%S')
            data.append([
                order.trading_pair,
                "buy" if order.is_buy else "sell",
                float(order.price),
                f"{spread:.2%}",
                float(order.quantity),
                float(size_q),
                age_txt
            ])
        df = pd.DataFrame(data=data, columns=columns)
        df.sort_values(by=["Market", "Side"], inplace=True)
        return df

    def budget_status_df(self) -> pd.DataFrame:
        data = []
        columns = ["Market", f"Budget({self._token})", "Base bal", "Quote bal", "Base/Quote"]
        for market, market_info in self._market_infos.items():
            mid_price = market_info.get_mid_price()
            base_bal = self._sell_budgets[market]
            quote_bal = self._buy_budgets[market]
            total_bal_in_quote = (base_bal * mid_price) + quote_bal
            total_bal_in_token = total_bal_in_quote
            if not self.is_token_a_quote_token():
                total_bal_in_token = base_bal + (quote_bal / mid_price)
            base_pct = (base_bal * mid_price) / total_bal_in_quote if total_bal_in_quote > 0 else s_decimal_zero
            quote_pct = quote_bal / total_bal_in_quote if total_bal_in_quote > 0 else s_decimal_zero
            data.append([
                market,
                float(total_bal_in_token),
                float(base_bal),
                float(quote_bal),
                f"{base_pct:.2%} / {quote_pct:.2%}"
            ])
        df = pd.DataFrame(data=data, columns=columns).replace(np.nan, '', regex=True)
        df.sort_values(by=["Market"], inplace=True)
        return df

    def market_status_df(self) -> pd.DataFrame:
        data = []
        columns = ["Market", "Mid price", "Best bid", "Best ask", "Bid %", "Ask %"]#, "Volatility"]
        for market, market_info in self._market_infos.items():
            mid_price = market_info.get_mid_price()
            best_bid = self._exchange.get_price(market, False)
            best_ask = self._exchange.get_price(market, True)
            best_bid_pct = abs(best_bid - mid_price) / mid_price
            best_ask_pct = (best_ask - mid_price) / mid_price
            data.append([
                market,
                float(mid_price),
                float(best_bid),
                float(best_ask),                
                f"{best_bid_pct:.3%}",
                f"{best_ask_pct:.3%}"
                #"" if self._volatility[market].is_nan() else f"{self._volatility[market]:.2%}",
            ])
        df = pd.DataFrame(data=data, columns=columns).replace(np.nan, '', regex=True)
        df.sort_values(by=["Market"], inplace=True)
        return df

    #async def miner_status_df(self) -> pd.DataFrame:
    #    data = []
    #    columns = ["Market", "Payout", "Reward/wk", "Liquidity", "Yield/yr", "Max spread"]
    #    campaigns = await get_campaign_summary(self._exchange.display_name, list(self._market_infos.keys()))
    #    for market, campaign in campaigns.items():
    #        reward_usd = await usd_value(campaign.payout_asset, campaign.reward_per_wk)
    #        data.append([
    #            market,
    #            campaign.payout_asset,
    #            f"${reward_usd:.0f}",
    #            f"${campaign.liquidity_usd:.0f}",
    #            f"{campaign.apy:.2%}",
    #            f"{campaign.spread_max:.2%}%"
    #        ])
    #    df = pd.DataFrame(data=data, columns=columns).replace(np.nan, '', regex=True)
    #    df.sort_values(by=["Market"], inplace=True)
    #    return df

    async def format_status(self) -> str:
        if not self._ready_to_trade:
            return "Market connectors are not ready."
        lines = []
        warning_lines = []
        warning_lines.extend(self.network_warning(list(self._market_infos.values())))

        budget_df = self.budget_status_df()
        lines.extend(["", "  Budget:"] + ["    " + line for line in budget_df.to_string(index=False).split("\n")])

        market_df = self.market_status_df()
        lines.extend(["", "  Markets:"] + ["    " + line for line in market_df.to_string(index=False).split("\n")])

        #miner_df = await self.miner_status_df()
        #if not miner_df.empty:
        #    lines.extend(["", "  Miner:"] + ["    " + line for line in miner_df.to_string(index=False).split("\n")])

        # See if there're any open orders.
        if len(self.active_orders) > 0:
            df = await self.active_orders_df()
            lines.extend(["", "  Orders:"] + ["    " + line for line in df.to_string(index=False).split("\n")])
        else:
            lines.extend(["", "  No active maker orders."])

        warning_lines.extend(self.balance_warning(list(self._market_infos.values())))
        if len(warning_lines) > 0:
            lines.extend(["", "*** WARNINGS ***"] + warning_lines)
        return "\n".join(lines)

    def start(self, clock: Clock, timestamp: float):
        time.sleep(1.0)
        restored_orders = self._exchange.limit_orders
        time.sleep(1.0)
        for order in restored_orders:
            self._exchange.cancel(order.trading_pair, order.client_order_id)

    def stop(self, clock: Clock):
        pass

    def create_base_proposals(self):
        proposals = []
        for market, market_info in self._market_infos.items():
            spread = self._spread
            #if not self._volatility[market].is_nan():
                # volatility applies only when it is higher than the spread setting.
                #spread = max(spread, self._volatility[market] * self._volatility_to_spread_multiplier)
            if self._max_spread > s_decimal_zero:
                spread = min(spread, self._max_spread)
            mid_price = market_info.get_mid_price()
            buy_price = mid_price * (Decimal("1") - spread)
            buy_price = self._exchange.quantize_order_price(market, buy_price)
            buy_size = self.base_order_size(market, buy_price)
            sell_price = mid_price * (Decimal("1") + spread)
            sell_price = self._exchange.quantize_order_price(market, sell_price)
            sell_size = self.base_order_size(market, sell_price)
            proposals.append(Proposal(market, PriceSize(buy_price, buy_size), PriceSize(sell_price, sell_size)))
        return proposals

    def apply_order_optimization(self, proposals: List[Proposal]):
        for proposal in proposals:       
            market_info = self._market_infos[proposal.market]
            mid_price = market_info.get_mid_price()
            depth_amount = self.base_order_size(proposal.market, mid_price) * self._order_optimization_depth_pct
            own_buy_qty = s_decimal_zero
            own_sell_qty = s_decimal_zero
            for order in self.active_orders:
                if order.trading_pair == proposal.market:
                    if order.is_buy:
                        own_buy_qty = order.quantity
                    else:
                        own_sell_qty = order.quantity
            
            # Get the top BID price in the market using order_optimization_depth and your BUY order volume
            top_bid_price = market_info.get_price_for_volume(False, depth_amount + own_buy_qty).result_price
            price_quantum = self._exchange.get_order_price_quantum(proposal.market, top_bid_price)
            price_above_bid = (ceil(top_bid_price / price_quantum) + 1) * price_quantum
            # If the price_above_bid is lower than the price suggested by the top pricing proposal,
            # lower the price and from there apply the order_level_spread to each order in the next levels
            lower_buy_price = proposal.buy.price
            if price_above_bid < lower_buy_price:
                lower_buy_price = price_above_bid
            elif self._order_optimization_failsafe_enabled:
                next_price = market_info.get_next_price(False, lower_buy_price).result_price
                next_price_quantum = self._exchange.get_order_price_quantum(proposal.market, next_price)
                lower_buy_price = (ceil(next_price / next_price_quantum) + 1) * next_price_quantum

            if self._max_spread > s_decimal_zero:
                lower_buy_price = min(lower_buy_price, mid_price * (Decimal("1") - self._max_spread))
            proposal.buy.price = self._exchange.quantize_order_price(proposal.market, lower_buy_price)
        
            # Get the top ASK price in the market using order_optimization_depth and your SELL order volume
            top_ask_price = market_info.get_price_for_volume(True, depth_amount + own_sell_qty).result_price
            price_quantum = self._exchange.get_order_price_quantum(proposal.market, top_ask_price)
            price_below_ask = (floor(top_ask_price / price_quantum) - 1) * price_quantum
            # If the price_below_ask is higher than the price suggested by the pricing proposal,
            # increase your price and from there apply the order_level_spread to each order in the next levels
            higher_sell_price = proposal.sell.price
            if price_below_ask > higher_sell_price:
                higher_sell_price = price_below_ask
            elif self._order_optimization_failsafe_enabled:
                next_price = market_info.get_next_price(True, higher_sell_price).result_price
                next_price_quantum = self._exchange.get_order_price_quantum(proposal.market, next_price)
                higher_sell_price = (floor(next_price / next_price_quantum) - 1) * next_price_quantum

            if self._max_spread > s_decimal_zero:
                higher_sell_price = min(higher_sell_price, mid_price * (Decimal("1") + self._max_spread))
            proposal.sell.price = self._exchange.quantize_order_price(proposal.market, higher_sell_price)

    def total_port_value_in_token(self) -> Decimal:
        all_bals = self.adjusted_available_balances()
        port_value = all_bals.get(self._token, s_decimal_zero)
        for market, market_info in self._market_infos.items():
            base, quote = market.split("-")
            if self.is_token_a_quote_token():
                port_value += all_bals[base] * market_info.get_mid_price()
            else:
                port_value += all_bals[quote] / market_info.get_mid_price()
        return port_value

    def create_budget_allocation(self):
        # Create buy and sell budgets for every market
        self._sell_budgets = {m: s_decimal_zero for m in self._market_infos}
        self._buy_budgets = {m: s_decimal_zero for m in self._market_infos}
        port_value = self.total_port_value_in_token()
        market_portion = port_value / len(self._market_infos)
        balances = self.adjusted_available_balances()
        for market, market_info in self._market_infos.items():
            base, quote = market.split("-")
            if self.is_token_a_quote_token():
                self._sell_budgets[market] = balances[base]
                buy_budget = market_portion - (balances[base] * market_info.get_mid_price())
                if buy_budget > s_decimal_zero:
                    self._buy_budgets[market] = buy_budget
            else:
                self._buy_budgets[market] = balances[quote]
                sell_budget = market_portion - (balances[quote] / market_info.get_mid_price())
                if sell_budget > s_decimal_zero:
                    self._sell_budgets[market] = sell_budget

    def base_order_size(self, trading_pair: str, price: Decimal = s_decimal_zero):
        base, quote = trading_pair.split("-")
        if self._token == base:
            return self._order_amount
        if price == s_decimal_zero:
            price = self._market_infos[trading_pair].get_mid_price()
        return self._order_amount / price

    def apply_budget_constraint(self, proposals: List[Proposal]):
        balances = self._token_balances.copy()
        for proposal in proposals:
            if balances[proposal.base()] < proposal.sell.size:
                proposal.sell.size = balances[proposal.base()]
            proposal.sell.size = self._exchange.quantize_order_amount(proposal.market, proposal.sell.size)
            balances[proposal.base()] -= proposal.sell.size

            quote_size = proposal.buy.size * proposal.buy.price
            quote_size = balances[proposal.quote()] if balances[proposal.quote()] < quote_size else quote_size
            buy_fee = estimate_fee(self._exchange.name, True)
            buy_size = quote_size / (proposal.buy.price * (Decimal("1") + buy_fee.percent))
            proposal.buy.size = self._exchange.quantize_order_amount(proposal.market, buy_size)
            balances[proposal.quote()] -= quote_size

    def is_within_tolerance(self, cur_orders: List[LimitOrder], proposal: Proposal):
        cur_buy = [o for o in cur_orders if o.is_buy]
        cur_sell = [o for o in cur_orders if not o.is_buy]
        if (cur_buy and proposal.buy.size <= 0) or (cur_sell and proposal.sell.size <= 0):
            return False
        if cur_buy and \
                abs(proposal.buy.price - cur_buy[0].price) / cur_buy[0].price > self._order_refresh_tolerance_pct:
            return False
        if cur_sell and \
                abs(proposal.sell.price - cur_sell[0].price) / cur_sell[0].price > self._order_refresh_tolerance_pct:
            return False
        return True

    def cancel_active_orders(self, proposals: List[Proposal]):
        self._trading_pairs_to_redo = []
        for proposal in proposals:
            to_cancel = False
            cur_orders = [o for o in self.active_orders if o.trading_pair == proposal.market]
            if cur_orders and any(self.order_age(o) > self._max_order_age for o in cur_orders):
                to_cancel = True
            elif self._refresh_times[proposal.market] <= self.current_timestamp and \
                    cur_orders and not self.is_within_tolerance(cur_orders, proposal):
                to_cancel = True
            if to_cancel:
                for order in cur_orders:
                    if order.trading_pair not in self._trading_pairs_to_redo:
                        self._trading_pairs_to_redo.append(order.trading_pair)
                        self._exchange.cancel_trading_pair(order.trading_pair)
                        # To place new order on the next tick               
                        self._refresh_times[order.trading_pair] = self.current_timestamp + 0.1
        #for proposal in proposals:
        #    to_cancel = False
        #    cur_orders = [o for o in self.active_orders if o.trading_pair == proposal.market]
        #    if cur_orders and any(self.order_age(o) > self._max_order_age for o in cur_orders):
        #        to_cancel = True
        #    elif self._refresh_times[proposal.market] <= self.current_timestamp and \
        #            cur_orders and not self.is_within_tolerance(cur_orders, proposal):
        #        to_cancel = True
        #    if to_cancel:
        #        for order in cur_orders:
        #            self.cancel_order(self._market_infos[proposal.market], order.client_order_id)
        #            # To place new order on the next tick
        #            self._refresh_times[order.trading_pair] = self.current_timestamp + 0.1

    def execute_orders_proposal(self, proposals: List[Proposal]):
        for proposal in proposals:
            if proposal.market not in self._trading_pairs_to_redo or self._refresh_times[proposal.market] > self.current_timestamp:
                continue
            mid_price = self._market_infos[proposal.market].get_mid_price()
            spread = s_decimal_zero
            if proposal.buy.size > 0:
                spread = abs(proposal.buy.price - mid_price) / mid_price
                self.logger().info(f"({proposal.market}) Creating a bid order {proposal.buy} value: "
                                   f"{proposal.buy.size * proposal.buy.price:.2f} {proposal.quote()} spread: "
                                   f"{spread:.2%}")
                self.buy_with_specific_market(
                    self._market_infos[proposal.market],
                    proposal.buy.size,
                    order_type=OrderType.LIMIT_MAKER,
                    price=proposal.buy.price
                )
            if proposal.sell.size > 0:
                spread = abs(proposal.sell.price - mid_price) / mid_price
                self.logger().info(f"({proposal.market}) Creating an ask order at {proposal.sell} value: "
                                   f"{proposal.sell.size * proposal.sell.price:.2f} {proposal.quote()} spread: "
                                   f"{spread:.2%}")
                self.sell_with_specific_market(
                    self._market_infos[proposal.market],
                    proposal.sell.size,
                    order_type=OrderType.LIMIT_MAKER,
                    price=proposal.sell.price
                )
            if proposal.buy.size > 0 or proposal.sell.size > 0:
                self._refresh_times[proposal.market] = self.current_timestamp + self._order_refresh_time

    def is_token_a_quote_token(self):
        quotes = self.all_quote_tokens()
        if len(quotes) == 1 and self._token in quotes:
            return True
        return False

    def all_base_tokens(self) -> Set[str]:
        tokens = set()
        for market in self._market_infos:
            tokens.add(market.split("-")[0])
        return tokens

    def all_quote_tokens(self) -> Set[str]:
        tokens = set()
        for market in self._market_infos:
            tokens.add(market.split("-")[1])
        return tokens

    def all_tokens(self) -> Set[str]:
        tokens = set()
        for market in self._market_infos:
            tokens.update(market.split("-"))
        return tokens

    def adjusted_available_balances(self) -> Dict[str, Decimal]:
        """
        Calculates all available balances, account for amount attributed to orders and reserved balance.
        :return: a dictionary of token and its available balance
        """
        tokens = self.all_tokens()
        adjusted_bals = {t: s_decimal_zero for t in tokens}
        total_bals = {t: s_decimal_zero for t in tokens}
        total_bals.update(self._exchange.get_all_balances())
        for token in tokens:
            avail_bal = self._exchange.get_available_balance(token)
            if token == self._token and 0 <= self._inventory_max_available_token_amount < avail_bal:
                adjusted_bals[token] = self._inventory_max_available_token_amount
            else:
                adjusted_bals[token] = avail_bal
        for order in self.active_orders:
            base, quote = order.trading_pair.split("-")
            if order.is_buy:
                adjusted_bals[quote] += order.quantity * order.price
            else:
                adjusted_bals[base] += order.quantity
        return adjusted_bals

    def apply_inventory_skew(self, proposals: List[Proposal]):
        for proposal in proposals:
            buy_budget = self._buy_budgets[proposal.market]
            sell_budget = self._sell_budgets[proposal.market]
            mid_price = self._market_infos[proposal.market].get_mid_price()
            total_order_size = proposal.sell.size + proposal.buy.size
            bid_ask_ratios = calculate_bid_ask_ratios_from_base_asset_ratio(
                float(sell_budget),
                float(buy_budget),
                float(mid_price),
                float(self._inventory_target_base_pct),
                float(total_order_size * self._inventory_range_multiplier)
            )
            proposal.buy.size *= Decimal(bid_ask_ratios.bid_ratio)
            proposal.sell.size *= Decimal(bid_ask_ratios.ask_ratio)

    def did_fill_order(self, event):
        order_id = event.order_id
        market_info = self.order_tracker.get_shadow_market_pair_from_order_id(order_id)
        if market_info is not None:
            if event.trade_type is TradeType.BUY:
                msg = f"({market_info.trading_pair}) Maker BUY order (price: {event.price}) of {event.amount} " \
                      f"{market_info.base_asset} is filled."
                self.log_with_clock(logging.INFO, msg)
                self.notify_hb_app(msg)
                self._buy_budgets[market_info.trading_pair] -= (event.amount * event.price)
                self._sell_budgets[market_info.trading_pair] += event.amount
            else:
                msg = f"({market_info.trading_pair}) Maker SELL order (price: {event.price}) of {event.amount} " \
                      f"{market_info.base_asset} is filled."
                self.log_with_clock(logging.INFO, msg)
                self.notify_hb_app(msg)
                self._sell_budgets[market_info.trading_pair] -= event.amount
                self._buy_budgets[market_info.trading_pair] += (event.amount * event.price)

    #def update_mid_prices(self):
    #    for market in self._market_infos:
    #        mid_price = self._market_infos[market].get_mid_price()
    #        self._mid_prices[market].append(mid_price)
    #        # To avoid memory leak, we store only the last part of the list needed for volatility calculation
    #        max_len = self._volatility_interval * self._avg_volatility_period
    #        self._mid_prices[market] = self._mid_prices[market][-1 * max_len:]

    #def update_volatility(self):
    #    self._volatility = {market: s_decimal_nan for market in self._market_infos}
    #    for market, mid_prices in self._mid_prices.items():
    #        last_index = len(mid_prices) - 1
    #        atr = []
    #        first_index = last_index - (self._volatility_interval * self._avg_volatility_period)
    #        first_index = max(first_index, 0)
    #        for i in range(last_index, first_index, self._volatility_interval * -1):
    #            prices = mid_prices[i - self._volatility_interval + 1: i + 1]
    #            if not prices:
    #                break
    #            atr.append((max(prices) - min(prices)) / min(prices))
    #        if atr:
    #            self._volatility[market] = mean(atr)
    #    if self._last_vol_reported < self.current_timestamp - self._volatility_interval:
    #        for market, vol in self._volatility.items():
    #            if not vol.is_nan():
    #                self.logger().info(f"{market} volatility: {vol:.2%}")
    #        self._last_vol_reported = self.current_timestamp

    def notify_hb_app(self, msg: str):
        if self._hb_app_notification:
            from hummingbot.client.hummingbot_application import HummingbotApplication
            HummingbotApplication.main_application()._notify(msg)
