import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc)


@dataclass
class Position:
    trade_id: str
    side: str
    opened_at: str
    entry_price: float
    quantity: float
    notional: float
    stop_price: float
    initial_stop_price: float
    target_price: float
    entry_fee: float
    rationale: str
    candle_time: str


class PaperEngine:
    def __init__(self, config, ledger):
        self.config = config
        self.ledger = ledger
        self.state_path = Path(config.data_dir) / "state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load()

    def _load(self):
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text())
            if state.get("position"):
                state["position"].pop("confidence", None)
                state["position"].setdefault(
                    "initial_stop_price", state["position"]["stop_price"]
                )
            return state
        return {
            "balance": self.config.paper_start_balance,
            "position": None,
            "last_exit_at": None,
            "last_decision_candle": None,
            "daily": {},
        }

    def _save(self):
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2))
        temporary.replace(self.state_path)

    @property
    def position(self):
        return Position(**self.state["position"]) if self.state["position"] else None

    def _daily(self, now):
        key = now.date().isoformat()
        daily = self.state["daily"].setdefault(
            key, {"pnl": 0.0, "trades": 0, "start_balance": self.state["balance"]}
        )
        daily.setdefault("start_balance", self.state["balance"] - daily.get("pnl", 0.0))
        return daily

    def can_open(self, decision, analysis, now=None):
        now = now or utc_now()
        if self.position:
            return False, "position already open"
        if self.state["last_decision_candle"] == analysis.candle_time:
            return False, "candle already evaluated"
        daily = self._daily(now)
        if daily["trades"] >= self.config.max_trades_per_day:
            return False, "daily trade limit reached"
        if daily["pnl"] <= -(daily["start_balance"] * self.config.max_daily_loss):
            return False, "daily loss limit reached"
        if decision.action not in {"LONG", "SHORT"}:
            return False, "decision is WAIT"
        if decision.action == "LONG" and analysis.trend_filter == "DOWN":
            return False, "long blocked by higher-timeframe downtrend"
        if decision.action == "SHORT" and analysis.trend_filter == "UP":
            return False, "short blocked by higher-timeframe uptrend"
        if self.state["last_exit_at"]:
            last = datetime.fromisoformat(self.state["last_exit_at"])
            if (now - last).total_seconds() < self.config.cooldown_minutes * 60:
                return False, "cooldown active"
        return True, "risk gates passed"

    def mark_decision(self, analysis, decision, account=None, result=""):
        self.state["last_decision_candle"] = analysis.candle_time
        self._save()
        self.ledger.record_event(analysis, result)

    def open(self, decision, analysis, market_price, now=None):
        now = now or utc_now()
        adverse = self.config.slippage_bps / 10_000
        entry = market_price * (1 + adverse if decision.action == "LONG" else 1 - adverse)
        technical_stop = getattr(analysis, "invalidation_price", None)
        if technical_stop is None:
            stop_distance = analysis.atr * self.config.stop_atr
            technical_stop = entry - stop_distance if decision.action == "LONG" else entry + stop_distance
        stop_distance = entry - technical_stop if decision.action == "LONG" else technical_stop - entry
        if stop_distance <= 0:
            return None
        risk_amount = self.state["balance"] * self.config.risk_per_trade
        stop_fill = technical_stop * (1 - adverse if decision.action == "LONG" else 1 + adverse)
        execution_loss = entry - stop_fill if decision.action == "LONG" else stop_fill - entry
        loss_per_unit = execution_loss + entry * self.config.taker_fee_rate + stop_fill * self.config.taker_fee_rate
        desired_net_reward = loss_per_unit * self.config.minimum_net_reward_risk
        fee = self.config.taker_fee_rate
        if decision.action == "LONG":
            gross_target = entry + stop_distance * self.config.reward_risk
            required_fill = (entry * (1 + fee) + desired_net_reward) / (1 - fee)
            minimum_net_target = required_fill / (1 - adverse)
            target = max(gross_target, minimum_net_target)
        else:
            gross_target = entry - stop_distance * self.config.reward_risk
            required_fill = (entry * (1 - fee) - desired_net_reward) / (1 + fee)
            maximum_net_target = required_fill / (1 + adverse)
            target = min(gross_target, maximum_net_target)
        quantity = risk_amount / loss_per_unit
        max_notional = self.state["balance"] * self.config.max_leverage
        quantity = min(quantity, max_notional / entry)
        notional = quantity * entry
        stop = technical_stop
        position = Position(
            trade_id=uuid.uuid4().hex[:12],
            side=decision.action,
            opened_at=now.isoformat(),
            entry_price=entry,
            quantity=quantity,
            notional=notional,
            stop_price=stop,
            initial_stop_price=stop,
            target_price=target,
            entry_fee=notional * self.config.taker_fee_rate,
            rationale=decision.rationale,
            candle_time=analysis.candle_time,
        )
        self.state["position"] = asdict(position)
        self._daily(now)["trades"] += 1
        self._save()
        self.ledger.open_trade(position, analysis)
        return position

    def check_exit(self, market_price, now=None):
        position = self.position
        if not position:
            return None
        now = now or utc_now()
        age_minutes = (now - datetime.fromisoformat(position.opened_at)).total_seconds() / 60
        if position.side == "LONG":
            reason = "TARGET" if market_price >= position.target_price else "STOP" if market_price <= position.stop_price else None
        else:
            reason = "TARGET" if market_price <= position.target_price else "STOP" if market_price >= position.stop_price else None
        if not reason and age_minutes + 0.001 >= self.config.max_hold_minutes:
            reason = "TIMEOUT"
        if not reason:
            return None
        adverse = self.config.slippage_bps / 10_000
        exit_price = market_price * (1 - adverse if position.side == "LONG" else 1 + adverse)
        direction = 1 if position.side == "LONG" else -1
        gross = (exit_price - position.entry_price) * position.quantity * direction
        exit_fee = exit_price * position.quantity * self.config.taker_fee_rate
        net = gross - position.entry_fee - exit_fee
        self.state["balance"] += net
        daily = self._daily(now)
        daily["pnl"] += net
        self.state["position"] = None
        self.state["last_exit_at"] = now.isoformat()
        self._save()
        result = {
            "closed_at": now.isoformat(), "exit_price": exit_price, "exit_reason": reason,
            "gross_pnl": gross, "exit_fee": exit_fee, "net_pnl": net,
            "balance": self.state["balance"], "hold_minutes": age_minutes,
        }
        self.ledger.close_trade(position.trade_id, result)
        return result

    def check_exit_candle(self, candle, now=None):
        """Evaluate one complete candle. If both levels touch, assume STOP first."""
        position = self.position
        if not position:
            return None
        now = now or candle.close_time.to_pydatetime()
        if position.side == "LONG":
            stop_hit = float(candle.low) <= position.stop_price
            target_hit = float(candle.high) >= position.target_price
        else:
            stop_hit = float(candle.high) >= position.stop_price
            target_hit = float(candle.low) <= position.target_price
        if stop_hit:
            return self._close_at(position.stop_price, "STOP", now)
        if target_hit:
            return self._close_at(position.target_price, "TARGET", now)
        initial_risk = abs(position.entry_price - position.initial_stop_price)
        trigger = initial_risk * self.config.breakeven_trigger_r
        adverse = self.config.slippage_bps / 10_000
        fee = self.config.taker_fee_rate
        if position.side == "LONG" and float(candle.high) >= position.entry_price + trigger:
            breakeven_fill = position.entry_price * (1 + fee) / (1 - fee)
            breakeven_stop = breakeven_fill / (1 - adverse)
            if breakeven_stop > position.stop_price:
                self.state["position"]["stop_price"] = breakeven_stop
                self._save()
        elif position.side == "SHORT" and float(candle.low) <= position.entry_price - trigger:
            breakeven_fill = position.entry_price * (1 - fee) / (1 + fee)
            breakeven_stop = breakeven_fill / (1 + adverse)
            if breakeven_stop < position.stop_price:
                self.state["position"]["stop_price"] = breakeven_stop
                self._save()
        age_minutes = (now - datetime.fromisoformat(position.opened_at)).total_seconds() / 60
        if age_minutes + 0.001 >= self.config.max_hold_minutes:
            return self._close_at(float(candle.close), "TIMEOUT", now)
        return None

    def _close_at(self, market_price, reason, now):
        position = self.position
        adverse = self.config.slippage_bps / 10_000
        exit_price = market_price * (1 - adverse if position.side == "LONG" else 1 + adverse)
        direction = 1 if position.side == "LONG" else -1
        gross = (exit_price - position.entry_price) * position.quantity * direction
        exit_fee = exit_price * position.quantity * self.config.taker_fee_rate
        net = gross - position.entry_fee - exit_fee
        self.state["balance"] += net
        self._daily(now)["pnl"] += net
        self.state["position"] = None
        self.state["last_exit_at"] = now.isoformat()
        self._save()
        result = {
            "closed_at": now.isoformat(), "exit_price": exit_price, "exit_reason": reason,
            "gross_pnl": gross, "exit_fee": exit_fee, "net_pnl": net,
            "balance": self.state["balance"],
            "hold_minutes": (now - datetime.fromisoformat(position.opened_at)).total_seconds() / 60,
        }
        self.ledger.close_trade(position.trade_id, result)
        return result
