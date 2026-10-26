# Деплой из GitHub: шаги 6–10

Ubuntu 24.04, 1 CPU, 1 ГиБ RAM + 3 ГиБ swap, SSD 25 ГБ.
Код доставляем через GitHub, запуск — Docker Compose. Все команды ниже,
кроме публикации в GitHub и проверки браузером, выполняются на VPS.

Замените `USERNAME/REPOSITORY` и `norma.example.com` своими значениями.
Предполагается SSH-пользователь с sudo. Публикацию репозитория на GitHub
выполняет владелец проекта; эта инструкция ничего не отправляет автоматически.

## 6. Подготовить сервер и скачать проект

### Проверить исходные условия

```bash
free -h
swapon --show
df -h /
sudo ss -lntp
```

Нужны около 3 ГиБ swap и свободные TCP-порты 80/443. Если swap уже настроен,
повторно создавать его не нужно. Если порты заняты, сначала выясните, какой
сервис их использует. В панели VPS разрешите TCP 80/443; порт 8000 не открывайте.
Если UFW уже включён, разрешите 80/tcp и 443/tcp, сохранив доступ по SSH.

### Установить Docker, если его ещё нет

Проверка:

```bash
sudo docker version
sudo docker compose version
```

Для новой Ubuntu 24.04 без Docker:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker compose version
```

### Клонировать репозиторий

Сначала опубликуйте на GitHub текущий проект, включая Dockerfile, compose.yaml,
.env.example, deploy/, код и тесты. `storage/` и настоящие секреты в Git не входят.

На VPS:

```bash
sudo install -d -m 0755 -o "$(id -u)" -g "$(id -g)" /opt/normacheck
git clone https://github.com/USERNAME/REPOSITORY.git /opt/normacheck
cd /opt/normacheck
```

Если репозиторий закрытый, используйте SSH-доступ с отдельным read-only deploy key.
Не записывайте токен в URL репозитория. Репозиторий на сервере используем для
получения кода; локальные правки исходников делаем на компьютере и отправляем
через GitHub.

### Подготовить данные и настройки

```bash
sudo install -d -m 0700 -o 10001 -g 10001 /var/lib/normacheck/storage
sudo install -d -m 0700 /etc/normacheck
```

Создать env из шаблона с новым секретом один раз; существующий файл команда
не перезапишет:

```bash
sudo python3 - <<'PY'
import os
from pathlib import Path
import secrets

text = Path('/opt/normacheck/.env.example').read_text()
text = text.replace('APP_SECRET=\n', 'APP_SECRET=' + secrets.token_urlsafe(48) + '\n')
fd = os.open('/etc/normacheck/app.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(text)
PY
sudo nano /etc/normacheck/app.env
```

Замените `DOMAIN=example.com` на свой домен. Оставьте `APP_IMAGE=normacheck:local`,
`COOKIE_SECURE=true`, `STORAGE_PATH=/var/lib/normacheck/storage` и стартовые
ограничения из шаблона. APP_SECRET при обновлении сохраняется.

В текущем SSH-терминале определите сокращение:

```bash
cd /opt/normacheck
dc() {
    sudo docker compose \
        --env-file /etc/normacheck/app.env \
        -p normacheck \
        -f /opt/normacheck/compose.yaml "$@"
}
dc config --quiet
```

После нового подключения SSH определите `dc` снова. `config --quiet` проверяет
конфигурацию, не выводя секрет. Файл env остаётся вне репозитория.

## 7. Собрать и проверить на VPS

```bash
cd /opt/normacheck
dc build app
sudo docker build --target test -t normacheck:test .
sudo docker run --rm --memory 640m --memory-swap 2560m --cpus 0.9 normacheck:test
```

Ожидается успешное завершение Python-тестов. Образы собираются под архитектуру
самого VPS. Основное хранилище в этих тестах не используется.

Для сквозной проверки через Caddy установите служебное Python-окружение:

```bash
sudo apt-get install -y python3-venv
python3 -m venv "$HOME/.cache/normacheck-check-venv"
"$HOME/.cache/normacheck-check-venv/bin/python" -m pip install -r /opt/normacheck/requirements.lock
sudo "$HOME/.cache/normacheck-check-venv/bin/python" \
    /opt/normacheck/deploy/check_compose.py --image normacheck:local
```

Ожидается `All Compose checks passed`. Скрипт создаёт отдельные временные данные,
секрет и loopback-порты; по завершении останавливает свой проект. Диагностика
остаётся в выведенном временном каталоге. Не запускайте две копии одновременно.

Во втором SSH-терминале во время проверки:

```bash
sudo docker stats
```

При необходимости также наблюдайте `vmstat 1` и проверяйте kernel log на OOM.
Короткая проверка подтверждает базовый сценарий, но не предельную нагрузку
на тяжёлых DOCX. Стартовый профиль: app 640 МиБ RAM, до 2560 МиБ RAM+swap,
два DOCX-слота; Caddy 96 МиБ RAM, до 192 МиБ RAM+swap. Постоянная активная
работа swap и аварийные перезапуски требуют пересмотра ресурсов до публикации.

## 8. Подключить домен и запустить сайт

В DNS-панели создайте A-запись выбранного домена на IPv4 VPS. Для первого
запуска используйте обычный DNS без CDN-прокси. AAAA оставляйте только при
отдельно проверенном доступе по IPv6: текущий Compose публикует порты на IPv4.

Проверьте, что DOMAIN в env совпадает с этим именем. Затем:

```bash
cd /opt/normacheck
dc config --quiet
dc pull caddy
dc up -d --no-build --wait --wait-timeout 120
dc ps
dc logs --tail 100 caddy
```

Caddy получает и обновляет HTTPS-сертификат. Если сертификат ещё не получен,
проверьте DNS, доступность портов и логи. Не удаляйте volumes сертификатов.

С компьютера:

```bash
curl -fsS https://norma.example.com/health
curl -sS -D - -o /dev/null http://norma.example.com/
curl -sS -o /dev/null -w '%{http_code}\n' https://norma.example.com/storage/.session-secret
curl --connect-timeout 3 http://VPS_IP:8000/health
```

Ожидаются: `{"status":"ok"}`, переход на HTTPS, 404 для storage и отсутствие
доступа к порту 8000. Не используйте `curl -k`: проверка сертификата должна проходить.
Docker публикует порты своими правилами, поэтому проверяйте доступность извне,
а не полагайтесь только на UFW.

## 9. Проверить сайт и перезапуск

Откройте сайт по HTTPS. Загрузите сгенерированный тестовый DOCX, получите отчёт,
выполните исправление и скачивание. Сохраните URL отчёта и обновите страницу:
в том же браузере документ должен оставаться доступным. В приватном окне
тот же URL не должен открывать документ.

Генерация тестового файла на компьютере из проекта:

```bash
.venv/bin/python - <<'PY'
from docx import Document

doc = Document()
p = doc.add_paragraph('Сгенерированный документ для проверки запуска NormaCheck.')
p.paragraph_format.line_spacing = 1.5
doc.save('/tmp/normacheck-public-check.docx')
PY
```

Файл, принятый основным экземпляром, останется в постоянном хранилище.
Нагрузочные проверки делайте отдельно, не используя пользовательские документы.

Когда нет активных операций, на VPS:

```bash
dc up -d --no-build --no-deps --force-recreate --wait --wait-timeout 90 app
```

Проверьте старый отчёт и скачивание в прежнем браузере. Затем, когда нет
незаконченных операций:

```bash
sudo reboot
```

После восстановления SSH снова определите `dc` и проверьте:

```bash
dc ps
swapon --show
curl -fsS https://norma.example.com/health
```

Проверьте прежний отчёт. Контейнеры, swap, сертификат и доступ к данным должны
восстановиться. Краткий 502 при загрузке возможен до готовности app; длительный
502 проверяйте по логам.

## 10. Обновлять и наблюдать

### Обновление из GitHub

На компьютере внесите изменения, проверьте их, сделайте commit и push.
На VPS, до остановки сайта:

```bash
cd /opt/normacheck
git rev-parse HEAD
sudo docker tag normacheck:local normacheck:previous
git pull --ff-only
dc config --quiet
dc build app
```

Запишите выведенный старый commit. Пока идёт сборка, старый контейнер работает
из прежнего образа. На сервере с 1 CPU сборку лучше делать при малой нагрузке.

После успешной сборки:

```bash
dc stop --timeout 220 caddy
dc stop --timeout 220 app
dc up -d --no-build --force-recreate --wait --wait-timeout 120
curl -fsS https://norma.example.com/health
```

Сначала закрывается вход через Caddy с ожиданием текущих запросов, затем
останавливается app. На время обновления сайт недоступен. Исходники, отчёты,
секрет и сертификаты сохраняются. Временная конфигурация обслуживания для
этого простого процесса не требуется.

Если новая сборка не прошла, старый сайт продолжает работать. Не переходите
к остановке, пока сборка не завершилась успешно. Если новый запуск не прошёл,
посмотрите `dc logs --tail 100 app` и `dc logs --tail 100 caddy`.

### Откат

Предыдущий образ сохранён как `normacheck:previous`. При необходимости остановите
сервисы командами выше, в защищённом env временно задайте
`APP_IMAGE=normacheck:previous`, затем выполните:

```bash
dc up -d --no-build --force-recreate --wait --wait-timeout 120
```

Не выполняйте сборку с этим тегом, чтобы не перезаписать предыдущий образ.
Если менялись Compose или Caddyfile, их также нужно вернуть к записанному старому
commit. Совместимость формата метаданных при будущих изменениях проверяется до
обновления. Накопленные документы при откате кода не откатываются и не удаляются.
После исправления кода верните `APP_IMAGE=normacheck:local`.

### Проверка состояния

```bash
dc ps
dc stats --no-stream
dc logs --tail 100 app
df -h /
free -h
sudo du -sh /var/lib/normacheck/storage
```

Следите за диском, OOM, перезапусками и частыми 503/504. Логи уже ограничены
в Compose. Первоначальный бюджет документов — 2048 МиБ; 507 может означать
достижение этого бюджета, даже если SSD ещё свободен. Документы для освобождения
места не удаляются. Healthcheck сам по себе не отправляет уведомления и не
перезапускает зависший процесс; внешний мониторинг /health настраивается отдельно.

### Резервные копии

GitHub хранит код, но не загруженные документы и секрет сессий. Для резервирования
нужны `/var/lib/normacheck/storage` и `/etc/normacheck/app.env`, а также сведения
о рабочей версии кода. Согласованную копию получайте после остановки Caddy/app,
затем запускайте их снова. Копию храните вне VPS с закрытым доступом; место и
автоматическое расписание копирования настраиваются отдельным этапом.
Восстановление сначала проверяйте в отдельном каталоге, не поверх живого storage.

При обычных обновлениях не используйте `down -v`, очистку volumes или удаление
storage. На сервере не должно быть двух API-процессов с одним хранилищем.

## Официальные источники

- https://docs.docker.com/engine/install/ubuntu/
- https://docs.docker.com/engine/network/packet-filtering-firewalls/
- https://caddyserver.com/docs/automatic-https
