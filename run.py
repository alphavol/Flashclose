import asyncio
import functools
from typing import Any, Dict, List, Optional
import ccxt.async_support as ccxt
import sys
import os
import platform

if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

live_tools_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'Live-Tools-V2'))
sys.path.append(live_tools_path)

from secret import ACCOUNTS

MAX_RETRIES = 5
BASE_DELAY = 1 

def retry_async(func):
    """Decorator for retrying async operations with exponential backoff"""

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        for attempt in range(MAX_RETRIES):
            try:
                return await func(self, *args, **kwargs)
            except Exception as e:
                is_rate_limit = isinstance(e, ccxt.RateLimitExceeded) or str(e).startswith('{"code":"429"')
                if is_rate_limit and attempt < MAX_RETRIES - 1:
                    delay = BASE_DELAY * (2**attempt)
                    print(f"Rate limit hit, attempt {attempt + 1}/{MAX_RETRIES}, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue
                raise Exception(f"BitgetExchange operation failed: {str(e)}") from e

    return wrapper


class BitgetExchange:
    def __init__(self, api_setup: Dict[str, Any], product_type: str = "USDT-FUTURES"):
        self.product_type = product_type
        self.margin_coin = "USDT" if product_type == "USDT-FUTURES" else "USDC"
        
        api_setup.update({
            "options": {"defaultType": "future"},
            "enableRateLimit": True,
            "rateLimit": 100,
        })
        self._exchange = ccxt.bitget(api_setup)
        self.markets = None

    async def __aenter__(self):
        await self.load_markets()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    @retry_async
    async def close(self) -> None:
        await self._exchange.close()

    @retry_async
    async def load_markets(self) -> None:
        self.markets = await self._exchange.load_markets()

    @retry_async
    async def fetch_open_positions(self) -> List[Dict[str, Any]]:
        params = {"productType": self.product_type}
        positions = await self._exchange.fetch_positions(params=params)
        open_positions = [
            pos for pos in positions 
            if pos.get("contracts") is not None and float(pos["contracts"]) > 0
        ]
        return open_positions

    @retry_async
    async def flash_close_position(self, symbol: str, side: str) -> None:
        try:
            await self._exchange.close_position(symbol, side=side)
        except Exception as e:
            raise

    @retry_async
    async def fetch_open_trigger_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {
            "productType": self.product_type,
            "planType": "normal_plan",  # Normal trigger orders
        }
        if symbol:
            params["symbol"] = self._format_symbol(symbol)
            
        response = await self._exchange.private_mix_get_v2_mix_order_orders_plan_pending(params)
        
        orders = []
        if "data" in response and "entrustedList" in response["data"]:
            orders = response["data"]["entrustedList"]
        
        return orders

    @retry_async
    async def fetch_open_tpsl_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {
            "productType": self.product_type,
            "planType": "profit_loss",  
        }
        if symbol:
            params["symbol"] = self._format_symbol(symbol)
            
        response = await self._exchange.private_mix_get_v2_mix_order_orders_plan_pending(params)
        
        orders = []
        if "data" in response and "entrustedList" in response["data"]:
            orders = response["data"]["entrustedList"]
        
        return orders

    @retry_async
    async def fetch_open_limit_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {
            "productType": self.product_type,
            "status": "live",
        }
        if symbol:
            params["symbol"] = self._format_symbol(symbol)
            
        try:
            response = await self._exchange.private_mix_get_v2_mix_order_orders_pending(params)
            
            if not response or "data" not in response:
                print("Warning: Unexpected response format when fetching limit orders")
                return []
                
            entrusted_list = response.get("data", {}).get("entrustedList", [])
            if entrusted_list is None:
                return []
                
            return [order for order in entrusted_list if order.get("orderType") == "limit"]
            
        except Exception as e:
            print(f"Error fetching limit orders: {str(e)}")
            return []

    @retry_async
    async def cancel_trigger_order(self, symbol: str, order_id: str) -> None:
        params = {
            "productType": self.product_type,
            "marginCoin": self.margin_coin,
            "symbol": self._format_symbol(symbol),
            "orderId": order_id
        }
        await self._exchange.private_mix_post_v2_mix_order_cancel_plan_order(params)

    @retry_async
    async def cancel_limit_order(self, symbol: str, order_id: str) -> None:
        params = {
            "productType": self.product_type,
            "marginCoin": self.margin_coin,
            "symbol": self._format_symbol(symbol),
            "orderId": order_id
        }
        await self._exchange.private_mix_post_v2_mix_order_cancel_order(params)

    def _format_symbol(self, symbol: str) -> str:
        """Format symbol for API calls if needed"""
        if '/' in symbol:
            return symbol.split(':')[0].replace('/', '')
        return symbol


def get_bitget_api_credentials() -> dict:
    try:
        key_name = "bitget1"
        account = ACCOUNTS.get(key_name, {})
        if not account or not account.get("public_api") or not account.get("secret_api"):
            raise Exception(f"Invalid API credentials for {key_name} in secret.py")
            
        return {
            "apiKey": account["public_api"],
            "secret": account["secret_api"],
            "password": account["password"]
        }
    except Exception as e:
        raise Exception(f"Failed to load API credentials: {str(e)}")


async def main():
    product_type = "USDT-FUTURES" 
    
    print("\nStarting Bitget Flash Close...")
    
    try:
        api_setup = get_bitget_api_credentials()
        
        async with BitgetExchange(api_setup, product_type=product_type) as exchange:
            
            # Step 1: Fetch and close all open positions
            print("\nFetching open positions...")
            positions = await exchange.fetch_open_positions()
            
            if not positions:
                print(" > No open positions found")
            else:
                print(f" > Found {len(positions)} open positions to close")
                
                for position in positions:
                    print("\n >>>>>> Closing position: ", position)
                    symbol = position['symbol']
                    side = position['side']
                    contracts = position['contracts']
                    
                    try:
                        await exchange.flash_close_position(symbol, side)
                        print(f" > Closed {side} position for {symbol} ({contracts} contracts)")
                    except Exception as e:
                        print(f"\n /!/ Error closing position for {symbol}: {str(e)}. \n The position details are: {position}")
            
            # Step 2: Cancel all trigger orders
            print("\nFetching trigger orders...")
            trigger_orders = await exchange.fetch_open_trigger_orders()
            
            if not trigger_orders:
                print(" > No open trigger orders found")
            else:
                print(f" > Found {len(trigger_orders)} trigger orders to cancel")
                
                for order in trigger_orders:
                    symbol = order['symbol']
                    order_id = order['orderId']
                    
                    try:
                        await exchange.cancel_trigger_order(symbol, order_id)
                        print(f" > Canceled trigger order {order_id} for {symbol}")
                    except Exception as e:
                        print(f" /!/ Error canceling trigger order {order_id}: {str(e)}")
            
            # Step 3: Cancel all TP/SL orders
            print("\nFetching TP/SL orders...")
            tpsl_orders = await exchange.fetch_open_tpsl_orders()
            
            if not tpsl_orders:
                print(" > No open TP/SL orders found")
            else:
                print(f" > Found {len(tpsl_orders)} TP/SL orders to cancel")
                
                for order in tpsl_orders:
                    symbol = order['symbol']
                    order_id = order['orderId']
                    
                    try:
                        await exchange.cancel_trigger_order(symbol, order_id)
                        print(f" > Canceled TP/SL order {order_id} for {symbol}")
                    except Exception as e:
                        print(f" /!/ Error canceling TP/SL order {order_id}: {str(e)}")
            
            # Step 4: Cancel all limit orders
            print("\nFetching limit orders...")
            limit_orders = await exchange.fetch_open_limit_orders()
            
            if not limit_orders:
                print(" > No open limit orders found")
            else:
                print(f" > Found {len(limit_orders)} limit orders to cancel")
                
                for order in limit_orders:
                    symbol = order['symbol']
                    order_id = order['orderId']
                    
                    try:
                        await exchange.cancel_limit_order(symbol, order_id)
                        print(f" > Canceled limit order {order_id} for {symbol}")
                    except Exception as e:
                        print(f" /!/ Error canceling limit order {order_id}: {str(e)}")
            
            # Final verification
            print("\nPerforming final verification...")
            positions = await exchange.fetch_open_positions()
            trigger_orders = await exchange.fetch_open_trigger_orders()
            tpsl_orders = await exchange.fetch_open_tpsl_orders()
            limit_orders = await exchange.fetch_open_limit_orders()
            
            if positions:
                print(f" /!/ WARNING: {len(positions)} positions still open")
                for position in positions:
                    print(f"  - {position['symbol']} {position['side']}: {position['contracts']} contracts")
            else:
                print(" > All positions closed successfully")
                
            if trigger_orders or tpsl_orders or limit_orders:
                print(f" /!/ WARNING: {len(trigger_orders) + len(tpsl_orders) + len(limit_orders)} orders still open")
                print(f"       - Trigger orders: {len(trigger_orders)}")
                print(f"       - TP/SL orders: {len(tpsl_orders)}")
                print(f"       - Limit orders: {len(limit_orders)}")
            else:
                print(" > All orders canceled successfully")
    
    except Exception as e:
        print(f"Error: {str(e)}")
    
    print("\n>>> Flash close operation completed!\n")


if __name__ == "__main__":
    asyncio.run(main())