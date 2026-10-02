# Запасной канал: логи через браузерную SSO-сессию (SAML / ADFS)

Читать, только если API логов без браузера отвечает 401 (кластер пускает исключительно через
SAML, локальных или сервисных учёток нет). Пока API отвечает напрямую — браузер не поднимать:
headless-браузер плюс полный SSO-логин стоят десятки секунд против долей секунды у `curl`, а
живая SSO-сессия никогда не гарантирована.

Постоянное решение — попросить владельцев кластера сервисную read-only учётку или API key;
тогда хватит `OPENSEARCH_TOKEN` / `OPENSEARCH_USER` и этот файл не нужен.

## 0. Проверить, что вход вообще возможен

```bash
python scripts/k8s_logs.py --check      # с SSO_HOST в окружении
```

`SSO … ConnectionRefused` / timeout → вход закрыт из этой сети (обычно нужен отдельный VPN).
Браузер, auth-vault и логин поднимать бессмысленно: браузер честно пройдёт предупреждение о
сертификате, откроет страницу логина кластера, нажмёт SSO и только там упрётся в
`ERR_CONNECTION_REFUSED`. TCP-проба на 443 говорит то же самое за секунду. Доступность самого
кластера ничего не говорит о доступности входа в него.

## 1. Инструмент и профиль входа

`agent-browser` (`npm i -g agent-browser && agent-browser install`) и один раз сохранённый
профиль в его auth-vault. **Пароль агент не вводит и не видит** — его вводит человек, профиль
хранится локально, форму заполняет vault.

```bash
agent-browser --version || (npm i -g agent-browser && agent-browser install)
agent-browser auth list | grep -q "$SSO_PROFILE" && echo OK || echo "NO PROFILE"
```

Профиля нет — проще всего скрипт (пароль скрытым вводом, в терминале человека):
```bash
bash scripts/setup-sso-profile.sh logs-sso "https://{SSO_HOST}/adfs/ls/"
```
Либо выведи человеку дословно, чтобы он выполнил сам:

> Нужен одноразовый профиль входа. Выполните в своём терминале, подставив свой логин и пароль:
> ```
> agent-browser auth save logs-sso --url "https://{SSO_HOST}/adfs/ls/" --username "<логин>" --password "<пароль>"
> ```
> Пароль хранится локально в vault agent-browser, агент его не видит.

## 2. Логин (идемпотентно, без человека)

Сначала probe — сессия может быть жива:
```bash
agent-browser eval "(async()=>{const r=await fetch('/api/console/proxy?path={index}%2F_count&method=POST',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','osd-xsrf':'true'},body:'{\"query\":{\"match_all\":{}}}'});return r.status})()"
# 200 → сессия жива, логин пропустить. 401 / страница логина → полный вход:
```

Пример для OpenSearch Dashboards + ADFS (IdP-initiated вход, затем тихий SP-вход):
```bash
agent-browser close --all
agent-browser open --headed --ignore-https-errors "{OPENSEARCH_URL}/app/login?nextUrl=%2Fapp%2Fhome"
# Chrome показал «Подключение не защищено» на самоподписанном сертификате — набрать вслепую: thisisunsafe

# a) ADFS-сессия через IdP-вход (auth login на голом /adfs/ls/ даёт страницу «Ошибка»):
agent-browser open "https://{SSO_HOST}/adfs/ls/idpinitiatedsignon.aspx"
agent-browser eval "document.getElementById('idp_SignInButton').click()"   # появится форма логина
agent-browser auth login logs-sso    # vault заполнит логин+пароль; «Timed out waiting for submit button» — норма
agent-browser eval "document.getElementById('submitButton').click()"       # сабмит

# b) SP-вход по живой ADFS-сессии:
agent-browser open "{OPENSEARCH_URL}/app/login?nextUrl=%2Fapp%2Fhome"
# взять href ссылки SAML-логина из snapshot (/auth/saml/captureUrlFragment?nextUrl=...) и ПЕРЕЙТИ по нему:
agent-browser open "{OPENSEARCH_URL}/auth/saml/captureUrlFragment?nextUrl=%2Fapp%2Fhome"
# итог: url = /app/home
```

При 401 посреди работы — повторить логин целиком, без участия человека.

## 3. Запрос из сессии

```js
(async () => {
  const q = {size: 10, sort: [{"@timestamp":"desc"}],
    _source: ["@timestamp","level","kubernetes.container.name","message","trace"],
    query: {bool: {filter: [
      {match_phrase: {"kubernetes.namespace": "<NS>"}},
      {match_phrase: {"kubernetes.container.name": "<CONTAINER>"}},
      {range: {"@timestamp": {gte: "<FROM>", lte: "<TO>"}}},
      {terms: {"level": ["error","warn"]}}]}}};
  const r = await fetch('/api/console/proxy?path=' + encodeURIComponent('<INDEX>/_search') + '&method=POST',
    {method:'POST', credentials:'same-origin',
     headers:{'Content-Type':'application/json','osd-xsrf':'true'}, body: JSON.stringify(q)});
  if (r.status === 401) return 'SESSION-EXPIRED';     // → §2 и повторить
  const d = await r.json();
  if (d.error) return 'ES-ERROR ' + JSON.stringify(d.error).slice(0, 300);
  return 'total=' + d.hits.total.value + '\n' + d.hits.hits.map(h =>
    [h._source['@timestamp'], h._source.level, String(h._source.message).slice(0,200)].join(' | ')).join('\n');
})()
```

Для Kibana (Elastic) из сессии — `/internal/search/es` с заголовками `kbn-xsrf`,
`x-elastic-internal-origin: Kibana`, `elastic-api-version: 1`, ответ в `rawResponse.hits`.
`console/proxy` в Kibana из сессии может отдать 403 — тогда только `/internal/search/es`.

## 4. Ловушки браузерного канала

1. `auth login` без IdP-шага навигирует на голый `/adfs/ls/` → страница «Ошибка», полей нет.
2. Клик по ref на кнопках ADFS и SAML-ссылке отвечает «Done», но ничего не происходит. Работают
   только `eval getElementById(...).click()` и переход по href.
3. `--ignore-https-errors` / `--headed` игнорируются, если демон уже запущен → сначала `close --all`.
4. Basic-auth и голый REST к кластеру с SAML → 401 с заголовком про IdP. Локальных пользователей
   нет — значит, только сессия или сервисная учётка.
5. Ссылка Discover в OpenSearch Dashboards без `?security_tenant=…` открывает не данные, а
   страницу управления index pattern.

## 5. Прод — только по явному запросу

- Отдельный хост (`{OPENSEARCH_PROD_URL}`), часто отдельный профиль входа (`kibana-prod`) и VPN.
- Прямой REST с логином/паролем на проде нередко не работает (SSO) — только браузерная сессия.
- Только чтение. Выводы для аналитика — без трейсов и стектрейсов, бизнес-языком.
- Имена индексов прода и теста могут совпадать — сверяй хост перед каждым выводом.
