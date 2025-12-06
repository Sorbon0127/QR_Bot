# Инструкция по развёртыванию QR Access System

## Структура проекта

```
qr-access-system/
├── services/
│   ├── users_service/          # Backend (FastAPI)
│   │   ├── main.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── models.py
│   └── qr_service/
├── gateway/
│   └── telegram_bot/            # Telegram бот
│       ├── app.py
│       ├── Dockerfile
│       └── requirements.txt
├── deploy/
│   ├── docker-compose.yml       # Оркестрация
│   ├── .env                     # Переменные окружения (не коммитить!)
│   └── .env.example             # Пример переменных
└── README.md
```

## Предварительные требования

- Docker Desktop (или Docker Engine на Linux)
- Docker Compose v2.0+
- Git

## Локальный запуск

### 1. Клонируй репозиторий

```bash
git clone https://github.com/Sorbon0127/QR_Bot.git
cd qr-access-system/deploy
```

### 2. Настрой переменные окружения

Скопируй пример и заполни свои значения:

```bash
cp .env.example .env
```

Отредактируй `.env`:

```
TELEGRAM_TOKEN=your_actual_telegram_token
```

**Где взять TELEGRAM_TOKEN:**
- Напиши @BotFather в Telegram
- Создай нового бота
- Скопируй токен из сообщения BotFather
- Вставь в `.env`

### 3. Запусти контейнеры

```bash
docker compose up -d --build
```

Флаги:
- `-d` — запустить в фоне
- `--build` — пересобрать образы (если меняли код)

### 4. Проверь логи

```bash
docker compose logs -f
```

или для отдельного сервиса:

```bash
docker compose logs -f users_service
docker compose logs -f telegram_bot
```

### 5. Проверь, что всё работает

- **Users Service:** http://localhost:8000/health
- **Docs:** http://localhost:8000/docs

Если видишь `{"status":"ok","service":"users_service"}` — всё ок.

## Остановка

```bash
docker compose down
```

Если нужно удалить данные (базу):

```bash
docker compose down -v
```

## После обновления кода

Если меняешь `main.py` или `app.py`:

```bash
docker compose up -d --build
```

Если обновления уже в DockerHub (после CI/CD):

```bash
docker compose pull
docker compose up -d
```

## Работа с DockerHub (для команды)

### Собрать образы вручную

Из корня проекта:

```bash
docker build -t kirito01277/qr-users-service:latest ./services/users_service
docker build -t kirito01277/qr-telegram-bot:latest ./gateway/telegram_bot
```

### Запушить в DockerHub

```bash
docker push kirito01277/qr-users-service:latest
docker push kirito01277/qr-telegram-bot:latest
```

### Потянуть образы команде

```bash
docker compose pull
docker compose up -d
```

## Использование образов из DockerHub (для деплоя)

Если хочешь использовать готовые образы из облака вместо локальной сборки, отредактируй `docker-compose.yml`:

Замени блок `services` на:

```yaml
services:
  users_service:
    image: kirito01277/qr-users-service:latest
    container_name: users_service
    env_file:
      - .env
    ports:
      - "8000:8000"

  telegram_bot:
    image: kirito01277/qr-telegram-bot:latest
    container_name: telegram_bot
    env_file:
      - .env
    environment:
      - USERS_SERVICE_URL=http://users_service:8000
    depends_on:
      - users_service
```

Тогда `docker compose up -d` будет тянуть образы с DockerHub.

## Логирование и отладка

### Просмотр всех контейнеров

```bash
docker ps -a
```

### Вход в контейнер

```bash
docker exec -it users_service bash
docker exec -it telegram_bot bash
```

### Просмотр логов с фильтром по времени

```bash
docker compose logs --since 10m
```

### Удаление всех неиспользуемых образов и контейнеров

```bash
docker system prune -a
```

## Решение проблем

### Ошибка: "port 8000 is already in use"

Измени порт в `docker-compose.yml`:

```yaml
ports:
  - "8001:8000"  # хост:контейнер
```

### Бот не отвечает

Проверь логи:

```bash
docker compose logs telegram_bot
```

Убедись, что `TELEGRAM_TOKEN` правильный в `.env`.

### Backend недоступен

Проверь health check:

```bash
curl http://localhost:8000/health
```

### База данных не сохраняется между перезапусками

Образы сейчас используют SQLite внутри контейнера. При `docker compose down -v` база удалится. Для продакшена нужна отдельная база (PostgreSQL в отдельном контейнере).

## CI/CD с Jenkins

После настройки Jenkins pipeline будет:

1. Подхватывать изменения из GitHub
2. Собирать образы
3. Пушить в DockerHub
4. На сервере автоматически запускать `docker compose pull && docker compose up -d`

Подробнее: см. раздел Jenkins в README.

---

**Автор:** QR Access System Team  
**Последнее обновление:** 2025-12-07