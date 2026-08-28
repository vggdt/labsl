-- Auto-generated seed data for the Cube semantic layer sample dataset.
-- Loads a small e-commerce dataset: customers, products, orders, order_items.

DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS order_items CASCADE;

CREATE TABLE customers (
  customer_id INTEGER PRIMARY KEY,
  first_name TEXT,
  last_name TEXT,
  email TEXT,
  city TEXT,
  state TEXT,
  signup_date DATE
);

CREATE TABLE products (
  product_id INTEGER PRIMARY KEY,
  name TEXT,
  category TEXT,
  price NUMERIC(10,2)
);

CREATE TABLE orders (
  order_id INTEGER PRIMARY KEY,
  customer_id INTEGER REFERENCES customers(customer_id),
  order_date DATE,
  status TEXT
);

CREATE TABLE order_items (
  order_item_id INTEGER PRIMARY KEY,
  order_id INTEGER REFERENCES orders(order_id),
  product_id INTEGER REFERENCES products(product_id),
  quantity INTEGER,
  unit_price NUMERIC(10,2)
);

INSERT INTO customers (customer_id, first_name, last_name, email, city, state, signup_date) VALUES
  (1, 'Ava', 'Nguyen', 'ava.nguyen@example.com', 'Seattle', 'WA', '2024-01-05'),
  (2, 'Liam', 'Smith', 'liam.smith@example.com', 'Austin', 'TX', '2024-01-12'),
  (3, 'Noah', 'Garcia', 'noah.garcia@example.com', 'Denver', 'CO', '2024-02-01'),
  (4, 'Emma', 'Johnson', 'emma.johnson@example.com', 'Seattle', 'WA', '2024-02-14'),
  (5, 'Olivia', 'Brown', 'olivia.brown@example.com', 'Miami', 'FL', '2024-03-03'),
  (6, 'Ethan', 'Davis', 'ethan.davis@example.com', 'Austin', 'TX', '2024-03-20'),
  (7, 'Sophia', 'Martinez', 'sophia.martinez@example.com', 'Denver', 'CO', '2024-04-02'),
  (8, 'Mason', 'Wilson', 'mason.wilson@example.com', 'Miami', 'FL', '2024-04-18'),
  (9, 'Isabella', 'Anderson', 'isabella.anderson@example.com', 'Chicago', 'IL', '2024-05-06'),
  (10, 'Lucas', 'Thomas', 'lucas.thomas@example.com', 'Chicago', 'IL', '2024-05-25');

INSERT INTO products (product_id, name, category, price) VALUES
  (1, 'Trail Running Shoes', 'Footwear', 89.99),
  (2, 'Insulated Jacket', 'Outerwear', 149.99),
  (3, 'Wireless Earbuds', 'Electronics', 59.99),
  (4, 'Yoga Mat', 'Fitness', 29.99),
  (5, 'Stainless Water Bottle', 'Accessories', 19.99),
  (6, 'Backpack 30L', 'Accessories', 74.99),
  (7, 'Smart Watch', 'Electronics', 199.99),
  (8, 'Rain Shell', 'Outerwear', 119.99),
  (9, 'Resistance Bands Set', 'Fitness', 15.99),
  (10, 'Trail Socks 3-Pack', 'Footwear', 17.99);

INSERT INTO orders (order_id, customer_id, order_date, status) VALUES
  (1, 1, '2024-02-01', 'completed'),
  (2, 2, '2024-02-03', 'completed'),
  (3, 1, '2024-02-20', 'completed'),
  (4, 3, '2024-03-05', 'completed'),
  (5, 4, '2024-03-10', 'cancelled'),
  (6, 5, '2024-03-15', 'completed'),
  (7, 6, '2024-03-22', 'completed'),
  (8, 2, '2024-04-01', 'completed'),
  (9, 7, '2024-04-10', 'processing'),
  (10, 8, '2024-04-15', 'completed'),
  (11, 3, '2024-04-28', 'completed'),
  (12, 9, '2024-05-08', 'completed'),
  (13, 10, '2024-05-12', 'completed'),
  (14, 1, '2024-05-20', 'completed'),
  (15, 5, '2024-05-30', 'cancelled'),
  (16, 6, '2024-06-04', 'completed'),
  (17, 9, '2024-06-11', 'processing'),
  (18, 4, '2024-06-18', 'completed'),
  (19, 7, '2024-06-25', 'completed'),
  (20, 10, '2024-07-02', 'completed');

INSERT INTO order_items (order_item_id, order_id, product_id, quantity, unit_price) VALUES
  (1, 1, 1, 1, 89.99),
  (2, 1, 5, 2, 19.99),
  (3, 2, 3, 1, 59.99),
  (4, 3, 6, 1, 74.99),
  (5, 4, 2, 1, 149.99),
  (6, 4, 10, 3, 17.99),
  (7, 5, 7, 1, 199.99),
  (8, 6, 4, 2, 29.99),
  (9, 7, 8, 1, 119.99),
  (10, 8, 1, 1, 89.99),
  (11, 8, 9, 2, 15.99),
  (12, 9, 3, 1, 59.99),
  (13, 10, 2, 1, 149.99),
  (14, 10, 5, 1, 19.99),
  (15, 11, 6, 1, 74.99),
  (16, 12, 10, 2, 17.99),
  (17, 13, 7, 1, 199.99),
  (18, 14, 1, 2, 89.99),
  (19, 15, 4, 1, 29.99),
  (20, 16, 8, 1, 119.99),
  (21, 17, 3, 2, 59.99),
  (22, 18, 9, 3, 15.99),
  (23, 19, 6, 1, 74.99),
  (24, 20, 2, 1, 149.99),
  (25, 20, 5, 3, 19.99);
