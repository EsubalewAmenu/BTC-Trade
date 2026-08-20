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
    target_price: float
    entry_fee: float
    confidence: float
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
            return json.loads(self.state_path.read_text())
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
        return self.state["daily"].setdefault(key, {"pnl": 0.0, "trades": 0})

    def can_open(self, decision, analysis, now=None):
        now = now or utc_now()
        if self.position:
            return False, "position already open"
        if self.state["last_decision_candle"] == analysis.candle_time:
            return False, "candle already evaluated"
        daily = self._daily(now)
        if daily["trades"] >= self.config.max_trades_per_day:
            return False, "daily trade limit reached"
        if daily["pnl"] <= -(self.config.paper_start_balance * self.config.max_daily_loss):
            return False, "daily loss limit reached"
        if decision.action not in {"LONG", "SHORT"}:
            return False, "decision is WAIT"
        if decision.confidence < self.config.min_llm_confidence:
            return False, "LLM confidence below threshold"
        if decision.action == "LONG" and analysis.trend_1h == "DOWN":
            return False, "long blocked by 1h downtrend"
        if decision.action == "SHORT" and analysis.trend_1h == "UP":
            return False, "short blocked by 1h uptrend"
        if self.state["last_exit_at"]:
            last = datetime.fromisoformat(self.state["last_exit_at"])
            if (now - last).total_seconds() < self.config.cooldown_minutes * 60:
                return False, "cooldown active"
        return True, "risk gates passed"

    def mark_decision(self, analysis, decision, account, result):
        self.state["last_decision_candle"] = analysis.candle_time
        self._save()
        self.ledger.record_event("DECISION", analysis, decision, account, result)

    def open(self, decision, analysis, market_price, now=None):
        now = now or utc_now()
        adverse = self.config.slippage_bps / 10_000
        entry = market_price * (1 + adverse if decision.action == "LONG" else 1 - adverse)
        stop_distance = analysis.atr * self.config.stop_atr
        risk_amount = self.state["balance"] * self.config.risk_per_trade
        quantity = risk_amount / stop_distance
        max_notional = self.state["balance"] * self.config.max_leverage
        quantity = min(quantity, max_notional / entry)
        notional = quantity * entry
        if decision.action == "LONG":
            stop, target = entry - stop_distance, entry + stop_distance * self.config.reward_risk
        else:
            stop, target = entry + stop_distance, entry - stop_distance * self.config.reward_risk
        position = Position(
            trade_id=uuid.uuid4().hex[:12],
            side=decision.action,
            opened_at=now.isoformat(),
            entry_price=entry,
            quantity=quantity,
            notional=notional,
            stop_price=stop,
            target_price=target,
            entry_fee=notional * self.config.taker_fee_rate,
            confidence=decision.confidence,
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
        if not reason and age_minutes >= self.config.max_hold_minutes:
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
