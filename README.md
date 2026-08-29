# Semantic Layer (Cube Core)

A [Cube](https://cube.dev) (open-source) semantic layer over a small sample
e-commerce dataset: `customers`, `products`, `orders`, `order_items`.

Cube sits between your database and BI tools / apps, and lets you define
metrics, dimensions, and joins once (in `model/`) and query them consistently
over REST, SQL, or GraphQL — instead of re-deriving the same business logic
in every dashboard or notebook.

## Project layout

```
model/
  cubes/          # one cube per source table: measures, dimensions, joins
    customers.yml
    products.yml
    orders.yml
    order_items.yml
  views/
    sales.yml      # the consumer-facing view — what BI tools should query
data/               # raw sample CSVs (source of truth for db/seed.sql)
db/
  seed.sql          # generated from data/*.csv — creates + loads Postgres tables
scripts/
  load-sample-data.sh   # loads db/seed.sql into a local Postgres you control
docker-compose.yml  # optional: Postgres + Cube, fully containerized
cube.js             # Cube server config (empty — defaults are used)
.env                 # local config, gitignored — copy from .env.example
```

## Data model

- **customers** — id, full_name, email, city, state, signup_date
- **products** — id, name, category, price
- **orders** — id, status, order_date, joined to customers
- **order_items** — id, quantity, unit_price, line_total, joined to orders and products
- **sales** (view) — the curated join of all four, exposed as the single
  entry point for BI tools (e.g. `sales.total_revenue` by `sales.products_category`)

`order_items` also defines one example pre-aggregation
(`revenue_by_day_and_category`) that rolls revenue up by day and product
category, so repeated queries at that grain are served from Cube Store
instead of re-hitting Postgres.

## Running it

You need Node.js and a Postgres database. Pick one of two ways to get one:

### Option A — local Postgres (matches "npm/CLI" dev flow)

```bash
cp .env.example .env          # edit CUBEJS_API_SECRET (openssl rand -hex 64)
./scripts/load-sample-data.sh # creates role/db, loads db/seed.sql
npm install
npm run dev
```

### Option B — Docker Compose (Postgres + Cube, no local Node needed)

```bash
cp .env.example .env
docker compose up
```

Either way, once running:

- Developer Playground: http://localhost:4000
- REST API: `http://localhost:4000/cubejs-api/v1/load`

Example query (revenue by product category):

```bash
curl -s -G http://localhost:4000/cubejs-api/v1/load \
  --data-urlencode 'query={"measures":["sales.total_revenue"],"dimensions":["sales.products_category"]}'
```

## Pointing this at your real data

Swap the sample dataset for your own warehouse:

1. Change `CUBEJS_DB_TYPE` and the `CUBEJS_DB_*` connection variables in
   `.env` to your database (Postgres, BigQuery, Snowflake, etc. — see
   [supported data sources](https://cube.dev/docs/product/configuration/data-sources)).
2. Replace `sql_table:` in each `model/cubes/*.yml` file with your real
   table names, or scaffold cubes from your existing schema via
   introspection: `npm run generate -- -t table1,table2` (reads the
   `CUBEJS_DB_*` connection from `.env`). Output is JavaScript (not YAML)
   and only includes joins (auto-detected from foreign keys) and raw
   per-column dimensions with a default `count` measure — you'll still
   need to add calculated fields, business-logic measures, and update
   `model/views/sales.yml` by hand.
3. Adjust joins, measures, and dimensions to match your schema, and update
   `model/views/sales.yml` to expose the fields your BI tools should see.

> `npm run generate` requires the `patches/@cubejs-backend+schema-compiler+*.patch`
> in this repo (applied automatically via `postinstall`) — `cubejs-cli@1.7.30`'s
> `generate` command has a packaging bug where it looks for
> `schema-compiler/scaffolding/ScaffoldingTemplate.js` at the package root,
> but the file only exists under `dist/src/`. The patch adds a small
> re-export shim at the expected path. Safe to drop once upstream fixes it.
