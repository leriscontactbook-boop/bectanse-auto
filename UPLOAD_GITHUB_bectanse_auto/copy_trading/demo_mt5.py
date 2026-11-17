"""MT5 demo-only adapter. Kept outside the read-only trading_journal package."""

import time
import ntpath
from contextlib import nullcontext
from datetime import datetime, timezone

from .demo_policy import SafetyError, decimal


class NotSent(SafetyError):
    """Preflight failed: order_send has definitely NOT been called."""


class DemoMT5:
    order_comment = 'BCT-DEMO'

    def __init__(self, module, *, login, server, terminal_path, writable=False,
                 max_spread_points=100, max_tick_age=5, clock=time.time, password=None,
                 broker_time_offset_seconds=0, enforce_spread_limit=True, require_stop_loss=True, enforce_master_price_limit=True, qualification=None, mapping=None, admission=None):
        if (type(broker_time_offset_seconds) is not int or
                abs(broker_time_offset_seconds) > 14 * 3600 or broker_time_offset_seconds % 900):
            raise ValueError("INVALID_BROKER_TIME_OFFSET")
        if type(enforce_spread_limit) is not bool:
            raise ValueError("INVALID_SPREAD_POLICY")
        if type(require_stop_loss) is not bool or type(enforce_master_price_limit) is not bool:
            raise ValueError("INVALID_DEMO_POLICY")
        self.admission = admission
        self.mapping = mapping
        self.qualification = qualification
        self.require_stop_loss = require_stop_loss
        self.enforce_master_price_limit = enforce_master_price_limit
        self.enforce_spread_limit = enforce_spread_limit
        self.broker_time_offset = broker_time_offset_seconds
        self.mt5, self.login, self.server = module, str(login), server
        self.password = password
        self.path, self.writable, self.clock = terminal_path, writable, clock
        self.max_spread, self.max_tick_age = decimal(max_spread_points), max_tick_age

    def connect(self):
        # Optional bootstrap credential is held in memory only, never in a manifest.
        credentials = {"password": self.password} if self.password else {}
        if not self.mt5.initialize(self.path, login=int(self.login), server=self.server,
                                   portable=True, timeout=30000, **credentials):
            # Error text may contain account/secret material: retain only a stable category.
            error = self.mt5.last_error()
            raise SafetyError("TERMINAL_AUTH_FAILED" if error and error[0] == -6
                              else "TERMINAL_CONNECTION_FAILED")
        self.guard()

    def guard(self):
        account, terminal = self.mt5.account_info(), self.mt5.terminal_info()
        if account is None or terminal is None or not terminal.connected:
            raise SafetyError("TERMINAL_DISCONNECTED")
        if str(account.login) != self.login or account.server != self.server:
            raise SafetyError("ACCOUNT_IDENTITY_CHANGED")
        if ntpath.normcase(ntpath.normpath(terminal.path)) != ntpath.normcase(ntpath.dirname(self.path)):
            raise SafetyError("TERMINAL_IDENTITY_CHANGED")
        self.check_account_mode(account)
        self.check_position_model(account)
        if self.writable and (not account.trade_allowed or not account.trade_expert
                              or not terminal.trade_allowed or terminal.tradeapi_disabled):
            raise SafetyError("TRADING_PERMISSION_DENIED")
        return account

    def check_position_model(self, account):
        if account.margin_mode != self.mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING:
            raise SafetyError("HEDGING_REQUIRED")
        if getattr(account, "fifo_close", False):
            raise SafetyError("FIFO_UNSUPPORTED")

    def check_account_mode(self, account):
        if account.trade_mode != self.mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise SafetyError("REAL_TRADING_FORBIDDEN")

    def spec(self, symbol):
        self.guard()
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise SafetyError("SYMBOL_UNAVAILABLE")
        return {key: getattr(info, key) for key in (
            "currency_base", "currency_profit", "trade_calc_mode", "trade_contract_size",
            "trade_tick_size", "point", "volume_min", "volume_max", "volume_step")}

    def native_orders(self):
        """Publish native pending events from the actual demo master adapter."""
        from .native_orders import read_native_orders
        return read_native_orders(self)

    def instrument_evidence(self, snapshot):
        from .instrument_collection import collect
        symbols=set(snapshot.get('instrument_symbols',[]))
        symbols.update(row['symbol'] for row in snapshot['positions'].values())
        symbols.update(row['symbol'] for row in snapshot.get('native_orders',{}).values())
        # Prepare Market Watch instruments before their first trade. Preserve
        # current/previous position symbols first when a catalog is large.
        catalog=self.mt5.symbols_get()
        if catalog is not None:
            visible=sorted(row.name for row in catalog if getattr(row,'visible',False))
            symbols.update(name for name in visible[:max(0,256-len(symbols))])
        same=self.positions()=={k:{f:v for f,v in row.items() if f!='spec'}
                                for k,row in snapshot['positions'].items()}
        orders=self.native_orders()
        expected={k:{f:v for f,v in row.items() if f not in {'spec','execution'}}
                  for k,row in snapshot.get('native_orders',{}).items()}
        reconciled=same and orders==expected and not snapshot.get('unresolved')
        return collect(self,symbols,reconciliation={'reconciled':reconciled,
                       'uncertain':not reconciled,'observed_at':self.clock()},clock=self.clock)

    def reconcile_native(self, **identity):
        from .native_orders import reconcile_native
        return reconcile_native(self, **identity)

    def readiness(self, symbols):
        """Read-only recovery evidence, including at least one fresh allowed market."""
        self.guard()
        positions = self.positions()
        orders = self.orders()
        self.deals(self.clock() - 86400)
        if self.unmanaged_orders(orders):
            raise SafetyError("BROKER_ORDER_PENDING")
        fresh = []
        for symbol in dict.fromkeys(symbols):
            info = self.mt5.symbol_info(symbol)
            if info is None or (not info.visible and not self.mt5.symbol_select(symbol, True)):
                continue
            tick = self.mt5.symbol_info_tick(symbol)
            if (tick is not None and -1 <= self.clock() -
                    (tick.time_msc / 1000 - self.broker_time_offset) <= self.max_tick_age
                    and decimal(tick.bid) > 0 and decimal(tick.ask) >= decimal(tick.bid)):
                fresh.append(symbol)
                break  # One fresh allowed market proves readiness; each entry checks its own quote.
        if not fresh:
            raise SafetyError("RECOVERY_QUOTES_UNAVAILABLE")
        self.guard()
        return {"positions": positions, "fresh_symbols": fresh}

    def position(self, row):
        if row.type not in (0, 1):
            raise SafetyError("UNSUPPORTED_POSITION_SIDE")
        return {"id": str(row.identifier), "ticket": int(row.ticket), "symbol": row.symbol,
                "side": "BUY" if row.type == 0 else "SELL", "volume": str(row.volume),
                "sl": str(row.sl), "tp": str(row.tp), "magic": int(row.magic),
                "price_open": str(row.price_open),
                "opened_at": row.time_msc / 1000 - self.broker_time_offset}

    def positions(self):
        self.guard()
        rows = self.mt5.positions_get()
        if rows is None:
            raise SafetyError("POSITIONS_UNAVAILABLE")
        self.guard()
        return {str(row.identifier): self.position(row) for row in rows}

    def orders(self):
        self.guard()
        rows = self.mt5.orders_get()
        if rows is None:
            raise SafetyError("ORDERS_UNAVAILABLE")
        return rows

    def native_orders(self):
        from .native_orders import read_native_orders
        return read_native_orders(self)

    def reconcile_native(self, **identity):
        from .native_orders import reconcile_native
        return reconcile_native(self, **identity)

    def unmanaged_orders(self, orders):
        return orders

    def deals(self, start):
        self.guard()
        rows = self.mt5.history_deals_get(datetime.fromtimestamp(start + self.broker_time_offset, timezone.utc),
                                          datetime.fromtimestamp(self.clock() + self.broker_time_offset + 1, timezone.utc))
        if rows is None:
            raise SafetyError("HISTORY_UNAVAILABLE")
        self.guard()
        return rows

    def position_closed(self, position_id):
        self.guard()
        rows = self.mt5.history_deals_get(position=int(position_id))
        if rows is None:
            raise SafetyError("HISTORY_UNAVAILABLE")
        entries = [row for row in rows if row.type in (0, 1)]
        if any(row.entry not in (0, 1, 3) for row in entries):
            raise SafetyError("UNSUPPORTED_POSITION_HISTORY")
        incoming = sum((decimal(row.volume) for row in entries if row.entry == 0), decimal(0))
        outgoing = sum((decimal(row.volume) for row in entries if row.entry in (1, 3)), decimal(0))
        return incoming > 0 and incoming == outgoing and str(position_id) not in self.positions()

    def recover(self, magic, since, symbol, side):
        positions = [p for p in self.positions().values() if p["magic"] == magic]
        if any(order.magic == magic for order in self.orders()):
            raise SafetyError("BROKER_ORDER_PENDING")
        entries = [row for row in self.deals(since - 300)
                   if row.magic == magic and row.type in (0, 1) and row.entry == 0]
        identities = {str(row.position_id) for row in entries} | {p["id"] for p in positions}
        if len(identities) > 1:
            raise SafetyError("AMBIGUOUS_EXECUTION")
        if not entries:
            return None  # No evidence means UNKNOWN, never permission to resend.
        if any(row.symbol != symbol or row.type != (0 if side == "BUY" else 1) for row in entries):
            raise SafetyError("EXECUTION_IDENTITY_MISMATCH")
        identity = identities.pop()
        if any(p["symbol"] != symbol or p["side"] != side for p in positions):
            raise SafetyError("EXECUTION_IDENTITY_MISMATCH")
        if not positions and not self.position_closed(identity):
            return None
        return {"id": identity, "position": positions[0] if positions else None,
                "opened_volume": str(sum((decimal(row.volume) for row in entries), decimal(0)))}

    def execute(self, logical):
        """Only pre-send failures are retryable. All results require reconciliation."""
        try:
            self._qualify(logical)
            request = self._prepare(logical)
            checked = self.mt5.order_check(request)
            if checked is None or checked.retcode != 0:
                raise SafetyError("ORDER_CHECK_REJECTED")
            self.guard()
            self._qualify(logical, request)
            if logical["kind"] == "OPEN" and self.clock() > logical["expires_at"]:
                raise SafetyError("ENTRY_EXPIRED")
        except SafetyError as exc:
            raise NotSent(str(exc)) from exc
        submitted = False
        # Do not catch errors here as NotSent: a lost response may hide a filled order.
        try:
            with self._submission_context(logical):
                submitted = True
                result = self.mt5.order_send(request)
        except NotSent as exc:
            if submitted:
                self._uncertain(logical)
                raise RuntimeError('EXECUTION_UNCERTAIN') from exc
            raise
        except Exception:
            self._uncertain(logical)
            raise
        if result is None or getattr(result, "retcode", None) in {10012, 10031}:
            self._uncertain(logical)
        return None if result is None else {key: int(getattr(result,key,0)) for key in ("retcode","order","deal","request_id")}

    def _submission_context(self, logical):
        if logical['kind'] != 'OPEN':
            return nullcontext()
        if self.admission is None:
            raise NotSent('ADMISSION_REQUIRED')
        return self.admission.submission()

    def _uncertain(self, logical):
        # Never turn a post-send journal/transport error into a retryable NotSent.
        self.qualification.uncertain(logical)

    def resolve_symbol(self, source_account, target_account, symbol, volume):
        if self.mapping is None:
            raise SafetyError("MAPPING_NOT_CONFIGURED")
        return self.mapping.resolve(source_account, target_account, symbol, volume)

    def _qualify(self, logical, request=None):
        if self.qualification is None:
            raise SafetyError("COMPATIBILITY_NOT_CONFIGURED")
        try:
            if logical.get("kind") == "OPEN":
                if self.mapping is None:
                    raise SafetyError("MAPPING_NOT_CONFIGURED")
                self.mapping.require_open(logical)
            self.qualification.require(logical, request)
        except SafetyError:
            raise
        except Exception:
            raise SafetyError("COMPATIBILITY_CHECK_FAILED") from None

    def _prepare(self, logical):
        self.guard()
        if not self.writable:
            raise SafetyError("OBSERVER_CANNOT_TRADE")
        kind, symbol = logical["kind"], logical["symbol"]
        if kind not in {"OPEN", "CLOSE", "MODIFY"}:
            raise SafetyError("UNSUPPORTED_ACTION")
        info = self.mt5.symbol_info(symbol)
        if info is None or (not info.visible and not self.mt5.symbol_select(symbol, True)):
            raise SafetyError("SYMBOL_UNAVAILABLE")
        tick = self.mt5.symbol_info_tick(symbol)
        def fresh(value):
            return value is not None and -1 <= self.clock() - (value.time_msc / 1000 - self.broker_time_offset) <= self.max_tick_age
        # Newly selected instruments may need their first quote from the broker.
        # Wait only before order_send, within the existing entry expiry budget.
        if kind == "OPEN" and not fresh(tick):
            if not self.mt5.symbol_select(symbol, True):
                raise SafetyError("SYMBOL_UNAVAILABLE")
            deadline = time.monotonic() + min(1.5, max(0, logical["expires_at"] - self.clock()))
            while not fresh(tick) and time.monotonic() < deadline and self.clock() < logical["expires_at"]:
                time.sleep(0.05)
                tick = self.mt5.symbol_info_tick(symbol)
        if not fresh(tick):
            raise SafetyError("STALE_QUOTE")
        bid, ask, point = decimal(tick.bid), decimal(tick.ask), decimal(info.point)
        if min(bid, ask, point) <= 0 or ask < bid:
            raise SafetyError("INVALID_QUOTE")
        if kind == "OPEN" and self.enforce_spread_limit and (ask - bid) / point > self.max_spread:
            raise SafetyError("SPREAD_LIMIT")
        if kind == "OPEN":
            reference = decimal(logical["reference_price"])
            quote = ask if logical["side"] == "BUY" else bid
            if reference <= 0 or (self.enforce_master_price_limit and abs(quote - reference) / point > decimal(logical["slippage"])):
                raise SafetyError("MASTER_PRICE_DEVIATION")
        position = None
        if kind != "OPEN":
            position = self.positions().get(logical["position_id"])
            if (position is None or position["magic"] != logical["magic"]
                    or position["symbol"] != symbol or position["side"] != logical["side"]):
                raise SafetyError("POSITION_IDENTITY_MISMATCH")
        request = {"symbol": symbol, "magic": logical["magic"], "comment": self.order_comment}
        if position:
            request["position"] = position["ticket"]  # current ticket, stable identifier stored separately
        if kind in {"OPEN", "MODIFY"}:
            sl, tp = decimal(logical["sl"]), decimal(logical["tp"])
            if sl < 0 or (self.require_stop_loss and sl == 0) or tp < 0:
                raise SafetyError("STOP_LOSS_REQUIRED")
            tick_size = decimal(info.trade_tick_size)
            if tick_size <= 0 or sl % tick_size or (tp and tp % tick_size):
                raise SafetyError("INVALID_STOP_TICK")
            distance = decimal(info.trade_stops_level) * point
            price = bid if logical["side"] == "BUY" else ask
            if logical["side"] == "BUY":
                valid = (sl == 0 or (sl < price and price - sl >= distance)) and (tp == 0 or (tp > price and tp - price >= distance))
            else:
                valid = (sl == 0 or (sl > price and sl - price >= distance)) and (tp == 0 or (tp < price and price - tp >= distance))
            if not valid:
                raise SafetyError("INVALID_STOP_DISTANCE")
            request.update(sl=float(sl), tp=float(tp))
        if kind == "MODIFY":
            request["action"] = self.mt5.TRADE_ACTION_SLTP
            return request
        # Prefer complete fills; IOC cancels any unfilled remainder. Never use RETURN,
        # which could leave a broker order working after this immediate operation.
        if info.filling_mode & 1:
            filling = self.mt5.ORDER_FILLING_FOK
        elif info.filling_mode & 2:
            filling = self.mt5.ORDER_FILLING_IOC
        else:
            raise SafetyError("IMMEDIATE_FILL_REQUIRED")
        volume = decimal(logical["volume"])
        step = decimal(info.volume_step)
        if (step <= 0 or volume % step or volume < decimal(info.volume_min)
                or volume > decimal(info.volume_max)):
            raise SafetyError("INVALID_ORDER_VOLUME")
        if position and volume > decimal(position["volume"]):
            raise SafetyError("CLOSE_EXCEEDS_POSITION")
        buy = (logical["side"] == "BUY") == (kind == "OPEN")
        request.update(action=self.mt5.TRADE_ACTION_DEAL, volume=float(volume),
                       type=self.mt5.ORDER_TYPE_BUY if buy else self.mt5.ORDER_TYPE_SELL,
                       price=float(ask if buy else bid), deviation=int(logical["slippage"]),
                       type_filling=filling, type_time=self.mt5.ORDER_TIME_GTC)
        return request

    def disconnect(self):
        self.mt5.shutdown()
