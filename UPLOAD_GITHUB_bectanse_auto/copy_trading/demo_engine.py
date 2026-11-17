"""Deterministic demo copy state machine, independent of network and terminal APIs."""

import hashlib
import time

from .demo_mt5 import NotSent
from .demo_policy import SafetyError, compatible, decimal, executable_settings, floor_volume, opening_volume, same_price


class Observer:
    def __init__(self, ledger, broker, source, clock=time.time):
        self.ledger, self.broker, self.source, self.clock = ledger, broker, source, clock
        self.continuity_started_at = clock()

    def cycle(self):
        started = self.clock()
        previous = self.ledger.get("source:" + self.source, {})
        if started - previous.get("observed_at", started) > 3:
            self.continuity_started_at = started
        positions = self.broker.positions()
        closed = set(previous.get("closed", []))
        missing = set(previous.get("positions", {})) - set(positions)
        # Keep missing positions as unresolved until broker history proves closure.
        unresolved = {identity for identity in previous.get("unresolved", [])
                      if not identity.startswith('order:')} | missing
        for identity in unresolved - set(positions):
            if self.broker.position_closed(identity):
                closed.add(identity)
        for position in positions.values():
            position["spec"] = self.broker.spec(position["symbol"])
        deals = self.broker.deals(previous.get("observed_at", started - 86400) - 60)
        audit = [{"ticket": str(row.ticket), "position_id": str(row.position_id),
                  "entry": row.entry, "volume": str(row.volume), "time_msc": row.time_msc}
                 for row in deals]
        snapshot = {"source": self.source, "observed_at": started,
                    "continuity_started_at": self.continuity_started_at, "positions": positions,
                    "closed": sorted(closed), "unresolved": sorted(unresolved - closed - set(positions))}
        if hasattr(self.broker,'native_orders') and hasattr(self.broker.mt5,'history_orders_get'):
            native=self.broker.native_orders()
            for row in native.values():row['spec']=self.broker.spec(row['symbol'])
            completed=dict(previous.get('native_completed',{}))
            awaiting={}
            for ticket,row in {**previous.get('native_pending_reconciliation',{}),
                               **previous.get('native_orders',{}),**native}.items():
                # Older observers persisted transient market orders here.
                # Their entry/exit deals are audited above, not reconciled as
                # pending entries (which correctly require DEAL_ENTRY_IN).
                if row['type'] not in range(2,8):
                    continue
                result=self.broker.reconcile_native(magic=row['magic'],symbol=row['symbol'],
                    # MT5 setup_msc is broker time; reconciliation accepts UTC.
                    order_type=row['type'],since=row['setup_msc']/1000
                    - getattr(self.broker,'broker_time_offset',0),ticket=ticket)
                if result is None:
                    if ticket not in native:
                        snapshot['unresolved'].append('order:'+ticket)
                        awaiting[ticket]=row
                elif ticket in native:
                    native[ticket]['execution']=result
                    if result['state']=='UNKNOWN':snapshot['unresolved'].append('order:'+ticket)
                elif result['state'] in {'FILLED','CANCELLED','EXPIRED','REJECTED'}:completed[ticket]=result
                else:
                    snapshot['unresolved'].append('order:'+ticket)
                    awaiting[ticket]=row
            snapshot.update(native_orders=native,native_completed=completed,
                            native_pending_reconciliation=awaiting)
        # Keep observing contracts after source closure: followers may still
        # need qualified management/reconciliation after a reconnect.
        tracked=set(previous.get('instrument_symbols',[]))
        for collection in ('positions','native_orders','native_pending_reconciliation'):
            tracked.update(row['symbol'] for row in snapshot.get(collection,{}).values())
        snapshot['instrument_symbols']=sorted(tracked)
        if hasattr(self.broker,'instrument_evidence'):
            try:snapshot['instrument_metadata']=self.broker.instrument_evidence(snapshot)
            except Exception:
                # Existing source events remain available. Missing instrument
                # evidence is an explicit denial at the qualification boundary.
                snapshot['instrument_metadata']={'source':'MT5_READ_ONLY','observed_at':started,
                                                 'error':'INSTRUMENT_COLLECTION_FAILED'}
        self.ledger.publish(self.source, snapshot, audit)
        return snapshot


class DemoEngine:
    mode = 'DEMO_ONLY'
    magic_namespace = 'BCT-DEMO'
    manage_when_paused = True
    def __init__(self, ledger, broker, *, source, owner, symbols, limits, clock=time.time):
        self.ledger, self.broker, self.source, self.owner = ledger, broker, source, owner
        self.symbols, self.limits, self.clock = symbols, limits, clock
        self.recovery_required = True
        ledger.pin_copy_identity(owner, {"source": source, "owner": owner, "symbols": symbols})

    def validate_settings(self, settings):
        return executable_settings(settings, require_stop_loss=self.limits.get("require_stop_loss", True))

    def suspend(self, reason):
        self.recovery_required = True
        self.ledger.put("recovery:" + self.owner, {"required": True, "reason": reason,
                                                  "at": self.clock()})

    def _save(self, identity, link):
        self.ledger.save_link(self.owner, identity, link)

    def _settle(self, identity, link):
        command = self.ledger.command(link["pending"])
        kind = command["kind"]
        if kind in {"OPEN", "PENDING_CREATE"}:
            found = self.broker.recover(link["magic"], command["created_at"], link["symbol"], link["side"])
            if not found:
                return False
            filled = decimal(found["opened_volume"])
            if filled <= 0 or filled > decimal(command["expected"]["volume"]):
                raise SafetyError("UNEXPECTED_FILLED_VOLUME")
            link.update(position_id=found["id"], initial_volume=str(filled),
                        state="OPEN" if found["position"] else "CLOSED_LOCAL")
        else:
            position = self.broker.positions().get(link["position_id"])
            if position is None:
                if not self.broker.position_closed(link["position_id"]):
                    return False
                link["state"] = "CLOSED"
            elif (position["magic"] != link["magic"] or position["symbol"] != link["symbol"]
                  or position["side"] != link["side"]):
                raise SafetyError("POSITION_IDENTITY_MISMATCH")
            elif kind == "CLOSE":
                if decimal(position["volume"]) > decimal(command["expected"]["remaining"]):
                    return False
            elif not (same_price(position["sl"], command["expected"]["sl"])
                      and same_price(position["tp"], command["expected"]["tp"])):
                return False
        link.pop("error", None)
        self.ledger.finish(self.owner, identity, link, command, "CONFIRMED")
        return True

    def _send(self, identity, link, kind, values, expected):
        request = {"kind": kind, "symbol": link["symbol"], "side": link["side"],
                   "source_symbol": link.get("source_symbol", ""),
                   "magic": link["magic"], "slippage": link["settings"]["slippage"], **values}
        if kind in {"CLOSE", "MODIFY"}:
            request["position_id"] = link["position_id"]
        command = self.ledger.intent(self.owner, identity, link, kind, request, expected, self.clock())
        try:
            result = self._execute_command(command)
        except NotSent as error:
            link["error"] = str(error)
            link["retry_after"] = self.clock() + 5
            if kind in {"OPEN", "PENDING_CREATE"}:
                link["state"] = "SKIPPED"  # never enter later at a different price without a new master trade
            self.ledger.finish(self.owner, identity, link, command, "NOT_SENT")
            return
        except Exception:
            # Intent deliberately remains SENDING. Never assume an exception means no fill.
            self.ledger.record_result(command, None)
            link["error"] = "EXECUTION_UNCERTAIN"
            self._save(identity, link)
            return
        self.ledger.record_result(command, result)
        # Only explicit no-execution server rejections with zero external IDs are terminal.
        # Timeouts, connectivity errors and unknown codes always require reconciliation.
        rejections={10004,10006,10013,10014,10015,10016,10017,10018,10019,10020,10021,10022,10030}
        if (result and result.get("retcode") in rejections
                and result.get("order")==0 and result.get("deal")==0):
            link["error"]="BROKER_REJECTED_"+str(result["retcode"])
            link["retry_after"]=self.clock()+5
            if kind in {"OPEN", "PENDING_CREATE"}:link["state"]="SKIPPED"
            self.ledger.finish(self.owner,identity,link,command,"REJECTED")
            return
        self._settle(identity, link)

    def cycle(self, configuration=None):
        try:
            self.broker.guard()
        except SafetyError as exc:
            self.suspend(str(exc))
            raise
        now = self.clock()
        snapshot = self.ledger.get("source:" + self.source)
        links = self.ledger.links(self.owner)
        errors = []
        # Reconcile outstanding operations even if the backend or master is offline.
        for identity, link in links.items():
            if link.get("pending"):
                try:
                    if not self._settle(identity, link):
                        errors.append("EXECUTION_UNCERTAIN")
                except SafetyError as exc:
                    errors.append(str(exc))
        # Native closure is an observation, not an execution permission. Settle
        # already closed copies even while entry/management authority is expired,
        # so qualification does not deadlock on a nonexistent owned position.
        for identity, link in self.ledger.links(self.owner).items():
            if link.get("state") != "OPEN" or link.get("pending"):
                continue
            try:
                pid = link["position_id"]
                if pid not in self.broker.positions() and self.broker.position_closed(pid):
                    link["state"] = "CLOSED_LOCAL"
                    self._save(identity, link)
            except SafetyError as exc:
                errors.append(str(exc))
        if not snapshot or not 0 <= now - snapshot["observed_at"] <= self.limits["snapshot_max_age"]:
            self.suspend("SOURCE_STALE")
            return self._report([*errors, "SOURCE_STALE"])
        positions = snapshot["positions"]
        baseline_key = "baseline:" + self.owner
        if not self.ledger.get(baseline_key):
            # Existing positions at first activation are intentionally never backfilled.
            for identity in positions:
                self._save(identity, {"state": "SKIPPED", "error": "INITIAL_BASELINE"})
            self.ledger.put(baseline_key, True)
            self.suspend("INITIAL_BASELINE")
            # Continue through the recovery preflight before accepting any later event.
            self._recover(positions, errors)
            return self._report(["BASELINE_ESTABLISHED"])

        settings = None
        opening_allowed = False
        if configuration:
            try:
                settings = self.validate_settings(configuration["settings"])
                opening_allowed = (configuration["enabled"] is True
                                   and now < configuration["valid_until"])
            except (ValueError, KeyError, SafetyError) as exc:
                errors.append(str(exc) if isinstance(exc, SafetyError) else "INVALID_CONFIGURATION")
        else:
            errors.append("CONFIGURATION_UNAVAILABLE")

        if snapshot.get("unresolved"):
            opening_allowed = False
            errors.append("SOURCE_RECONCILIATION_REQUIRED")
        if not opening_allowed:
            self.suspend("ENTRIES_PAUSED")
        elif self.recovery_required or self.ledger.get("recovery:" + self.owner, {}).get("required"):
            self._recover(positions, errors)
            opening_allowed = not self.recovery_required

        # Frozen settings stay attached to each existing copy; changing lots never resizes it up.
        for identity, link in self.ledger.links(self.owner).items():
            if not opening_allowed and not self.management_allowed(configuration):
                continue
            if link["state"] != "OPEN" or link.get("pending") or now < link.get("retry_after", 0):
                continue
            try:
                self._manage(identity, link, positions.get(identity), identity in snapshot["closed"])
            except SafetyError as exc:
                link["error"] = str(exc)
                self._save(identity, link)
                errors.append(str(exc))

        for identity, source_position in positions.items():
            if identity in self.ledger.links(self.owner):
                continue
            try:
                if not opening_allowed:
                    raise SafetyError("ENTRIES_PAUSED")
                if any(link.get("pending") for link in self.ledger.links(self.owner).values()):
                    raise SafetyError("UNRESOLVED_EXECUTION")
                if source_position["opened_at"] < snapshot.get("continuity_started_at", -1):
                    raise SafetyError("SOURCE_RECOVERY_BASELINE")
                self._open(identity, source_position, settings, configuration)
            except SafetyError as exc:
                # A skipped position is never reopened when settings/network recover.
                if identity not in self.ledger.links(self.owner):
                    self._save(identity, {"state": "SKIPPED", "error": str(exc)})
                errors.append(str(exc))
        return self._report(errors)

    def management_allowed(self, configuration):
        return self.manage_when_paused

    def _recover(self, positions, errors):
        # Freeze every already-observed source BEFORE checking readiness; no replay on recovery.
        for identity in positions:
            if identity not in self.ledger.links(self.owner):
                self._save(identity, {"state": "SKIPPED", "error": "RECOVERY_BASELINE"})
        try:
            if errors or any(link.get("pending") for link in self.ledger.links(self.owner).values()):
                raise SafetyError("RECOVERY_RECONCILIATION_REQUIRED")
            evidence = self.broker.readiness(self.symbols.values())
            known = {link.get("position_id"): link for link in self.ledger.links(self.owner).values()
                     if link.get("state") == "OPEN"}
            from .external_positions import isolated
            external=isolated(self.broker,evidence["positions"],self.ledger.links(self.owner))
            if set(evidence["positions"]) - set(known) - external:
                raise SafetyError("DEDICATED_ACCOUNT_REQUIRED")
            for position_id, actual in evidence["positions"].items():
                if position_id in external:continue
                link = known[position_id]
                if any(actual[k] != link[k] for k in ("magic", "symbol", "side")):
                    raise SafetyError("POSITION_IDENTITY_MISMATCH")
        except SafetyError as exc:
            errors.append(str(exc))
            self.suspend(str(exc))
            return
        self.recovery_required = False
        self.ledger.put("recovery:" + self.owner, {"required": False, "at": self.clock(),
                                                  "fresh_symbols": evidence["fresh_symbols"]})

    def _requested_volume(self, settings, source, lots):
        return settings['lots'] if settings['mode_risque']=='Lots fixes' else settings['lot_symboles'].get(source['symbol'],0)

    def _entry_source(self, source, settings):
        return source

    def _entry_volume(self, source, settings, target, spec):
        return opening_volume(settings, source['symbol'], spec, self.limits['max_lots_per_trade'],
                              self.limits.get('allow_broker_minimum', False),
                              exact_member_volume=self.limits.get('exact_member_volume', False)), {}

    def _open(self, identity, source, settings, configuration):
        now = self.clock()
        if not -1 <= now - source["opened_at"] <= self.limits["entry_max_age"]:
            raise SafetyError("ENTRY_EXPIRED")
        target = self.symbols.get(source["symbol"])
        if not target:raise SafetyError('SYMBOL_NOT_MAPPED')
        spec = self.broker.spec(target)
        lots, sizing = self._entry_volume(source, settings, target, spec)
        source = self._entry_source(source, settings)
        # Actual MT5 adapter requires a per-account registry before any preparation.
        # Existing-position management deliberately keeps its recorded target symbol.
        if hasattr(self.broker, "resolve_symbol"):
            requested = self._requested_volume(settings, source, lots)
            qualified_target = self.broker.resolve_symbol(self.source, self.owner, source["symbol"], requested)
            if target != qualified_target:
                raise SafetyError("MAPPING_CONFIG_MISMATCH")
        if not target:
            raise SafetyError("SYMBOL_NOT_MAPPED")
        spec = self.broker.spec(target)
        compatible(source["spec"], spec)
        if decimal(source["sl"]) < 0 or (self.limits.get("require_stop_loss", True) and decimal(source["sl"]) == 0) or decimal(source["volume"]) <= 0:
            raise SafetyError("STOP_LOSS_REQUIRED")
        actual = self.broker.positions()
        known = {link.get("position_id") for link in self.ledger.links(self.owner).values()}
        orders=self.broker.orders()
        blocking_orders=self.broker.unmanaged_orders(orders) if hasattr(self.broker,"unmanaged_orders") else orders
        from .external_positions import isolated
        external=isolated(self.broker,actual,self.ledger.links(self.owner))
        if set(actual) - known - external or blocking_orders:
            raise SafetyError("DEDICATED_ACCOUNT_REQUIRED")
        total = sum((decimal(p["volume"]) for p in actual.values()), decimal(0)) + sum((decimal(o.volume_current) for o in orders), decimal(0))
        total_cap = decimal(self.limits["max_total_lots"])
        if self.limits.get("allow_broker_minimum", False):
            total_cap = max(total_cap, decimal(spec["volume_min"]))
        if self.limits.get("enforce_exposure_limits", True) and (len(actual)+len(orders) >= self.limits["max_positions"] or total + lots > total_cap):
            raise SafetyError("EXPOSURE_LIMIT")
        magic = int(hashlib.sha256(f"{self.magic_namespace}:{self.source}:{self.owner}:{identity}".encode()).hexdigest()[:15], 16)
        link = {"state": "OPENING", "symbol": target, "side": source["side"], "magic": magic,
                "source_symbol": source["symbol"],
                "source_volume": source["volume"], "settings": settings, "sizing": sizing,
                "config_version": configuration["version"], "created_at": now}
        # Check for an earlier broker execution even if a restored local ledger lacks its link.
        if self.broker.recover(magic, source["opened_at"], target, source["side"]):
            raise SafetyError("PREEXISTING_EXECUTION")
        self._send(identity, link, "OPEN", {
            "requested_volume": str(self._requested_volume(settings, source, lots)),
            "volume": str(lots), "sl": source["sl"] if settings["copier_sl"] else "0", "tp": source["tp"],
            "reference_price": source["price_open"],
            "expires_at": min(source["opened_at"] + self.limits["entry_max_age"], configuration["valid_until"]),
        }, {"volume": str(lots)})

    def _manage(self, identity, link, source, closed):
        actual = self.broker.positions().get(link["position_id"])
        if actual is None:
            if self.broker.position_closed(link["position_id"]):
                link["state"] = "CLOSED_LOCAL"
                self._save(identity, link)
            else:
                raise SafetyError("FOLLOWER_POSITION_MISSING")
            return
        if actual["magic"] != link["magic"] or actual["symbol"] != link["symbol"] or actual["side"] != link["side"]:
            raise SafetyError("POSITION_IDENTITY_MISMATCH")
        if not source and not closed:
            raise SafetyError("SOURCE_CLOSURE_UNCONFIRMED")
        initial = decimal(link["initial_volume"])
        spec = self.broker.spec(link["symbol"])
        if closed:
            target = decimal(0)
        else:
            if source["side"] != link["side"] or decimal(source["volume"]) > decimal(link["source_volume"]):
                raise SafetyError("SOURCE_VOLUME_INCREASE_UNSUPPORTED")
            target = floor_volume(initial * decimal(source["volume"]) / decimal(link["source_volume"]), spec)
        current = decimal(actual["volume"])
        if current > initial:
            raise SafetyError("FOLLOWER_VOLUME_INCREASED")
        if current > target:
            # Reduce only complete lot steps; never round a close upward into a reversal.
            closing = floor_volume(current - target, spec)
            if closing <= 0:
                raise SafetyError("PARTIAL_CLOSE_BELOW_MINIMUM")
            self._send(identity, link, "CLOSE", {"volume": str(closing)}, {"remaining": str(current - closing)})
        elif source:
            desired_sl=source["sl"] if link["settings"]["copier_sl"] else actual["sl"]
            if not same_price(actual["sl"],desired_sl) or not same_price(actual["tp"],source["tp"]):
                self._send(identity, link, "MODIFY", {"sl": desired_sl, "tp": source["tp"]},
                           {"sl": desired_sl, "tp": source["tp"]})

    def _execute_command(self, command):
        return self.broker.execute(command['request'])

    def _report(self, errors):
        links = self.ledger.links(self.owner)
        pending = sum(bool(link.get("pending")) for link in links.values())
        active = sum(link["state"] == "OPEN" for link in links.values())
        current_errors = [link["error"] for link in links.values()
                          if link.get("error") and link["state"] in {"OPEN", "OPENING"}]
        return {"mode": self.mode, "open_positions": active, "pending_commands": pending,
                "errors": sorted(set(errors + current_errors + (["EXECUTION_UNCERTAIN"] if pending else []))),
                "positions": [{"source_id": identity, "position_id": link.get("position_id"),
                               "symbol": link.get("symbol"), "state": link["state"],
                               "version": link.get("config_version"), "error": link.get("error", "")}
                              for identity, link in list(links.items())[-50:]]}
