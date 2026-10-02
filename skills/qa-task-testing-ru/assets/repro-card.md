<!--
Карточка воспроизведения. Заполняй после каждого живого действия на стенде (сообщение в очередь,
вызов API с побочным эффектом, SQL-фикстура). По ней человек повторит прогон руками без тебя.
Без неё прогон непрозрачен: результат нельзя ни проверить, ни воспроизвести.
Ничего не сокращай многоточием — только целиком.
-->

### Карточка воспроизведения: <название проверки>

**Стенд:** {STAND} · **Ветка / версия сервиса:** <PROJ-123 @ commit> · **Клиент / сущность:** <id>
**Спека:** <ссылка на страницу и шаг или файл:строка>

#### 1. Исходное состояние БД (проверить ДО)
```sql
-- конкретный SELECT
SELECT id, status, payment_status, updated_at FROM orders.orders WHERE id = <orderId>;
```
Фактический результат: <строки целиком>
Ожидаемое: <что должно быть, чтобы проверка имела смысл>

#### 2. Создание тестовых данных (если нужны)
```sql
INSERT INTO orders.payments (order_id, status, provider_code, external_id, created_at, updated_at)
VALUES (<orderId>, 'CREATED', '<provider>', 'qa-PROJ-123-<время>', NOW(), NOW());
```

#### 3. Действие
Вариант A — сообщение в очередь:
- URL: {RABBITMQ_URL}/#/exchanges/{vhost}/<exchange>
- Exchange: <orders.events>
- Routing key: <orders.payment.confirmed>
- Заголовки: `__TypeId__` = <com.example.orders.event.PaymentConfirmedEvent>
- Payload:
```json
{ "полный": "payload без сокращений" }
```

Вариант B — HTTP-вызов:
```bash
curl -sS -X POST "http://<service>.{STAND}.{STAND_DOMAIN}/<path>" \
  -H "Content-Type: application/json" \
  -d '<тело целиком>'
```
Ответ: <HTTP-код + тело целиком, даже если он не подтверждает ожидание>

#### 4. Проверка результата (подождать 15–20 с, если обработка асинхронная)
```sql
SELECT id, status, cancel_reason, trace_id FROM orders.payments WHERE external_id = 'qa-PROJ-123-<время>';
```
Фактический результат: <строки>
Ожидаемый результат: <по спеке, со ссылкой>

#### 5. Трейс
{JAEGER_URL}/trace/<traceId>
Ошибки в трейсе: <каждая — текст лога + классификация ENV / тестовые данные / логика>

#### 6. Откат фикстур
```sql
-- откатывать ВСЕ поля, которые трогал сервис, а не только бизнес-статус
-- (служебные и hash-влияющие поля, даты окончания действия записи)
DELETE FROM orders.payments WHERE external_id = 'qa-PROJ-123-<время>';
UPDATE orders.orders SET status = '<исходное>', payment_status = '<исходное>', updated_at = '<исходное>' WHERE id = <orderId>;
```
SELECT после отката: <совпадает с п. 1 — да / нет>
