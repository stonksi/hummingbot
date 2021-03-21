from decimal import Decimal
from hummingbot.strategy.market_trading_pair_tuple import MarketTradingPairTuple
from hummingbot.strategy.stonksi_market_making.stonksi_market_making import StonksiMarketMakingStrategy
from hummingbot.strategy.stonksi_market_making.stonksi_market_making_config_map import stonksi_market_making_config_map as c_map


def start(self):
    exchange = c_map.get("exchange").value.lower()
    el_markets = list(c_map.get("markets").value.split(","))
    token = c_map.get("token").value.upper()
    el_markets = [m.upper() for m in el_markets]
    quote_markets = [m for m in el_markets if m.split("-")[1] == token]
    base_markets = [m for m in el_markets if m.split("-")[0] == token]
    markets = quote_markets if quote_markets else base_markets
    order_amount = c_map.get("order_amount").value
    spread = c_map.get("spread").value / Decimal("100")
    inventory_skew_enabled = c_map.get("inventory_skew_enabled").value
    inventory_target_base_pct = c_map.get("inventory_target_base_pct").value / Decimal("100")
    inventory_range_multiplier = c_map.get("inventory_range_multiplier").value
    order_refresh_time = c_map.get("order_refresh_time").value
    order_refresh_tolerance_pct = c_map.get("order_refresh_tolerance_pct").value / Decimal("100")
    #volatility_interval = c_map.get("volatility_interval").value
    #avg_volatility_period = c_map.get("avg_volatility_period").value
    #volatility_to_spread_multiplier = c_map.get("volatility_to_spread_multiplier").value
    max_spread = c_map.get("max_spread").value / Decimal("100")
    max_order_age = c_map.get("max_order_age").value
    order_optimization_enabled = c_map.get("order_optimization_enabled").value
    order_optimization_depth_pct = c_map.get("order_optimization_depth_pct").value / Decimal("100")

    self._initialize_markets([(exchange, markets)])
    exchange = self.markets[exchange]
    market_infos = {}
    for market in markets:
        base, quote = market.split("-")
        market_infos[market] = MarketTradingPairTuple(exchange, market, base, quote)
    self.strategy = StonksiMarketMakingStrategy(
        exchange=exchange,
        market_infos=market_infos,
        token=token,
        order_amount=order_amount,
        spread=spread,
        inventory_skew_enabled=inventory_skew_enabled,
        inventory_target_base_pct=inventory_target_base_pct,
        inventory_range_multiplier=inventory_range_multiplier,
        order_refresh_time=order_refresh_time,
        order_refresh_tolerance_pct=order_refresh_tolerance_pct,
        #volatility_interval=volatility_interval,
        #avg_volatility_period=avg_volatility_period,
        #volatility_to_spread_multiplier=volatility_to_spread_multiplier,
        max_spread=max_spread,
        max_order_age=max_order_age,
        order_optimization_enabled=order_optimization_enabled,
        order_optimization_depth_pct=order_optimization_depth_pct,
        hb_app_notification=True
    )
