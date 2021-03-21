from decimal import Decimal
from typing import Optional
from hummingbot.client.config.config_var import ConfigVar
from hummingbot.client.config.config_validators import (
    validate_exchange,
    validate_decimal,
    validate_int,
    validate_bool
)
from hummingbot.client.settings import (
    required_exchanges,
)


def exchange_on_validated(value: str) -> None:
    required_exchanges.append(value)


def token_validate(value: str) -> Optional[str]:
    value = value.upper()
    markets = list(stonksi_market_making_config_map["markets"].value.split(","))
    tokens = set()
    for market in markets:
        tokens.update(set(market.split("-")))
    if value not in tokens:
        return f"Invalid token. {value} is not one of {','.join(tokens)}"


def order_size_prompt() -> str:
    token = stonksi_market_making_config_map["token"].value
    return f"What is the size of each order (in {token} amount)? >>> "


stonksi_market_making_config_map = {
    "strategy": ConfigVar(
        key="strategy",
        prompt="",
        default="stonksi_market_making"),
    "exchange":
        ConfigVar(key="exchange",
                  prompt="Enter the spot connector to use for liquidity mining >>> ",
                  validator=validate_exchange,
                  on_validated=exchange_on_validated,
                  prompt_on_new=True),
    "markets":
        ConfigVar(key="markets",
                  prompt="Enter a list of markets (comma separated, e.g. LTC-USDT,ETH-USDT) >>> ",
                  type_str="str",
                  prompt_on_new=True),
    "token":
        ConfigVar(key="token",
                  prompt="What asset (base or quote) do you want to use to provide liquidity? >>> ",
                  type_str="str",
                  validator=token_validate,
                  prompt_on_new=True),
    "order_amount":
        ConfigVar(key="order_amount",
                  prompt=order_size_prompt,
                  type_str="decimal",
                  validator=lambda v: validate_decimal(v, 0, inclusive=False),
                  prompt_on_new=True),
    "spread":
        ConfigVar(key="spread",
                  prompt="How far away from the mid price do you want to place bid and ask orders? "
                         "(Enter 1 to indicate 1%) >>> ",
                  type_str="decimal",
                  validator=lambda v: validate_decimal(v, 0, 100, inclusive=False),
                  prompt_on_new=True),
    "inventory_skew_enabled":
        ConfigVar(key="inventory_skew_enabled",
                  prompt="Would you like to enable inventory skew? (Yes/No) >>> ",
                  type_str="bool",
                  default=True,
                  validator=validate_bool),
    "inventory_target_base_pct":
        ConfigVar(key="inventory_target_base_pct",
                  prompt="For each pair, what is your target base asset percentage? (Enter 20 to indicate 20%) >>> ",
                  type_str="decimal",
                  validator=lambda v: validate_decimal(v, 0, 100, inclusive=False),
                  prompt_on_new=True),
    "inventory_range_multiplier":
        ConfigVar(key="inventory_range_multiplier",
                  prompt="What is your tolerable range of inventory around the target, "
                         "expressed in multiples of your total order size? ",
                  type_str="decimal",
                  validator=lambda v: validate_decimal(v, min_value=0, inclusive=False),
                  default=Decimal("1")),
    "order_refresh_time":
        ConfigVar(key="order_refresh_time",
                  prompt="How often do you want to cancel and replace bids and asks "
                         "(in seconds)? >>> ",
                  type_str="float",
                  validator=lambda v: validate_decimal(v, 0, inclusive=False),
                  default=10.),
    "order_refresh_tolerance_pct":
        ConfigVar(key="order_refresh_tolerance_pct",
                  prompt="Enter the percent change in price needed to refresh orders at each cycle "
                         "(Enter 1 to indicate 1%) >>> ",
                  type_str="decimal",
                  default=Decimal("0.2"),
                  validator=lambda v: validate_decimal(v, -10, 10, inclusive=True)),
    #"volatility_interval":
    #    ConfigVar(key="volatility_interval",
    #              prompt="What is an interval, in second, in which to pick historical mid price data from to calculate "
    #                     "market volatility? >>> ",
    #              type_str="int",
    #              validator=lambda v: validate_int(v, min_value=1, inclusive=False),
    #              default=60 * 5),
    #"avg_volatility_period":
    #    ConfigVar(key="avg_volatility_period",
    #              prompt="How many interval does it take to calculate average market volatility? >>> ",
    #              type_str="int",
    #              validator=lambda v: validate_int(v, min_value=1, inclusive=False),
    #              default=10),
    #"volatility_to_spread_multiplier":
    #    ConfigVar(key="volatility_to_spread_multiplier",
    #              prompt="Enter a multiplier used to convert average volatility to spread "
    #                     "(enter 1 for 1 to 1 conversion) >>> ",
    #              type_str="decimal",
    #              validator=lambda v: validate_decimal(v, min_value=0, inclusive=False),
    #              default=Decimal("1")),
    "max_spread":
        ConfigVar(key="max_spread",
                  prompt="What is the maximum spread? (Enter 1 to indicate 1% or -1 to ignore this setting) >>> ",
                  type_str="decimal",
                  validator=lambda v: validate_decimal(v),
                  default=Decimal("-1")),
    "max_order_age":
        ConfigVar(key="max_order_age",
                  prompt="What is the maximum life time of your orders (in seconds)? >>> ",
                  type_str="float",
                  validator=lambda v: validate_decimal(v, min_value=0, inclusive=False),
                  default=60. * 60.),
    "order_optimization_enabled":
        ConfigVar(key="order_optimization_enabled",
                  prompt="Do you want to enable best bid ask jumping? (Yes/No) >>> ",
                  type_str="bool",
                  default=False,
                  validator=validate_bool),
    "order_optimization_depth_pct":
        ConfigVar(key="order_optimization_depth_pct",
                  prompt="How deep do you want to go into the order book for calculating "
                         "the top ask/bid, ignoring dust orders on the top "
                         "(expressed in percentage of order amount, 0 = disabled)? >>> ",
                  required_if=lambda: stonksi_market_making_config_map.get("order_optimization_enabled").value,
                  type_str="decimal",
                  validator=lambda v: validate_decimal(v, 0, 100),
                  default=0),
}
