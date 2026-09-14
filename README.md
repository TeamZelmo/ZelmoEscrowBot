# EscrowGuard — Telegram Escrow Bot

A production-ready starting point for a Telegram bot that acts as a trusted
middleman between a buyer and a seller, holding funds "in escrow" until the
buyer confirms delivery.

## Project structure

```
escrow_bot/
├── main.py              # Entry point: builds the Application, registers handlers, starts polling
├── database.py           # Motor (async MongoDB) connection + all CRUD for users/deals
├── keyboards.py           # Persistent bottom Reply Keyboard + dynamic Inline Keyboards
├── handlers/
│   ├── start.py           # /start, /help, /profile
│   ├── deal.py             # /create_deal, guided creation flow, /my_deals, /view_deal
│   └── escrow.py           # join / pay / release / dispute / resolve / cancel state machine
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

1. **Create a bot** with [@BotFather](https://t.me/BotFather) and copy the token.
2. **Install MongoDB** locally, or use a free [MongoDB Atlas](https://www.mongodb.com/atlas) cluster.
3. **Clone and configure:**
   ```bash
   cp .env.example .env
   # then edit .env: set BOT_TOKEN, MONGO_URI, DB_NAME, ADMIN_IDS
   ```
4. **Install dependencies** (Python 3.10+ recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
5. **Run it:**
   ```bash
   python main.py
   ```

## How a deal flows

1. **Buyer creates a deal** — `/create_deal 150 USDT Selling a laptop`, or tap
   **🤝 Create Deal** for a guided, step-by-step version. Status: `AWAITING_SELLER`.
2. **Seller joins** — taps **🙋 Join as Seller** on the deal message. Status: `AWAITING_PAYMENT`.
3. **Buyer pays** — taps **💳 Pay Now**. This calls `simulate_payment()` in
   `handlers/escrow.py`, which is a clearly marked integration point for a
   real payment gateway or blockchain deposit check. Status: `FUNDS_HELD`.
4. **Seller delivers** the goods/service off-platform (shipping, file
   transfer, account handover, etc. — outside the bot's scope).
5. **Buyer releases funds** — taps **✅ Release Funds**. Status: `RELEASED`.
   *— or —* **either party raises a dispute** with **⚠️ Raise Dispute**.
   Status: `DISPUTED`, and every admin ID in `ADMIN_IDS` is notified with
   inline **Favor Buyer / Favor Seller** buttons (an equivalent
   `/resolve_dispute <deal_id> <buyer|seller>` command also exists).
6. **Cancellation** is only allowed while no funds are held (`AWAITING_SELLER`
   or `AWAITING_PAYMENT`), by the buyer or an admin.

## Design notes

- **Multi-currency**: `deal.currency` stores a plain code (`USD`, `INR`,
  `USDT`, ...). `handlers/deal.py::SUPPORTED_CURRENCIES` is the single place
  to add more. If you need live FX conversion (e.g. to show a USD-equivalent
  next to a USDT amount), add a `get_exchange_rate(base, quote)` utility and
  call it wherever you display converted amounts — none of the escrow logic
  needs to change since everything is stored in the deal's own currency.
- **Authorization**: every state-changing handler in `escrow.py` re-checks
  `update.effective_user.id` against `deal['buyer_id']` / `deal['seller_id']`
  / `ADMIN_IDS` before doing anything — inline buttons are just an entry
  point, never a source of trust.
- **Race-condition safety**: `database.update_deal_status()` and
  `database.assign_seller()` both use MongoDB's atomic
  `find_one_and_update`, conditioned on the deal's *expected* current
  status. If two people tap conflicting buttons at once, only the first
  write wins and the second caller gets a "state changed, please refresh"
  message instead of corrupting the deal.
- **Persistence across restarts**: all deal/user state lives in MongoDB, not
  in memory, so the bot can restart or scale horizontally without losing
  in-progress deals. `context.user_data` is only used transiently, for the
  guided deal-creation conversation's in-progress answers.
- **Payment simulation**: `simulate_payment()` in `handlers/escrow.py` is a
  placeholder that always succeeds. Before going live, replace it with a
  real integration (Stripe, a card processor, or verifying an on-chain
  USDT/BTC/ETH deposit) and only return `True` once funds have
  irreversibly arrived.

## Security checklist before production

- [ ] Replace `simulate_payment()` with a real, verified payment/deposit check.
- [ ] Restrict `ADMIN_IDS` to people you trust with dispute resolution.
- [ ] Run MongoDB with auth enabled and a least-privilege user for the bot.
- [ ] Consider rate-limiting `/create_deal` per user to deter spam/abuse.
- [ ] Log to a persistent, monitored destination (not just stdout).
- [ ] Add automated backups for the `deals` collection — it's your ledger.
