from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from datetime import datetime
import csv
import pandas as pd
from io import StringIO
import io
import logging
import Levenshtein

from database import Base, engine, SessionLocal
from models import Guest, Mark, TelegramUser

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Users Service", version="0.6.1")

# Создаем таблицы
Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class GuestCreate(BaseModel):
    code: str
    name: str


class MarkRequest(BaseModel):
    code: str
    method: str = "qr"


class SearchResult(BaseModel):
    code: str
    name: str
    scanned: bool


class TelegramUserCreate(BaseModel):
    telegram_id: Optional[int] = None
    username: Optional[str] = None  # без @
    name: str
    allowed: bool = True


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "users_service"}


@app.post("/mark")
def mark_guest(req: MarkRequest, db: Session = Depends(get_db)):
    code = req.code.strip()
    logger.info(f"Mark request for code: {code}")

    guest = db.query(Guest).filter(Guest.code == code).first()
    if not guest:
        logger.warning(f"Code not found: {code}")
        raise HTTPException(status_code=404, detail="Код не найден")

    name = guest.name
    now = datetime.now()

    mark = db.query(Mark).filter(Mark.code == code).first()
    already_marked = mark is not None

    if not mark:
        mark = Mark(
            code=code,
            name=name,
            method=req.method,
            timestamp=now,
        )
        db.add(mark)
        logger.info(f"New mark created for code: {code}")
    else:
        mark.name = name
        mark.method = req.method
        mark.timestamp = now
        logger.info(f"Mark updated for code: {code}")

    db.commit()
    db.refresh(mark)

    return {
        "status": "ok",
        "message": "Отметка сохранена",
        "already_marked": already_marked,
        "data": {
            "code": mark.code,
            "name": mark.name,
            "timestamp": mark.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "method": mark.method,
        },
    }


@app.post("/import_excel")
async def import_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    logger.info(f"Importing Excel file: {file.filename}")

    filename = file.filename.lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".xls")):
        raise HTTPException(status_code=400, detail="Ожидается Excel-файл (.xlsx или .xls)")

    content = await file.read()

    try:
        df = pd.read_excel(io.BytesIO(content))
        logger.info(f"Excel file read successfully. Columns: {list(df.columns)}")
    except Exception as e:
        logger.error(f"Error reading Excel: {e}")
        raise HTTPException(status_code=400, detail=f"Не удалось прочитать Excel: {str(e)}")

    available_cols = [col.lower().strip() for col in df.columns]
    logger.info(f"Available columns (lowercase): {available_cols}")

    code_col = None
    name_col = None

    possible_code_names = ["код"]
    possible_name_names = ["фио"]

    for col in df.columns:
        col_lower = col.lower().strip()
        if col_lower in possible_code_names and not code_col:
            code_col = col
            logger.info(f"Found code column: {col}")
        elif col_lower in possible_name_names and not name_col:
            name_col = col
            logger.info(f"Found name column: {col}")

    if not code_col or not name_col:
        if len(df.columns) >= 2:
            code_col = df.columns[0]
            name_col = df.columns[1]
            logger.info(f"Using first two columns: code={code_col}, name={name_col}")
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Не найдены нужные колонки. Доступные колонки: {list(df.columns)}",
            )

    added_guests = 0
    errors = []
    total_rows = len(df)

    for index, row in df.iterrows():
        try:
            code_val = row[code_col]
            name_val = row[name_col]

            code = str(code_val).strip() if not pd.isna(code_val) else ""
            name = str(name_val).strip() if not pd.isna(name_val) else ""

            logger.debug(f"Processing row {index}: code='{code}', name='{name}'")

            if not name:
                errors.append(f"Строка {index+2}: пустое имя")
                continue

            if not code:
                code = f"NAME-{int(datetime.now().timestamp())}-{index}"

            exists = db.query(Guest).filter(Guest.code == code).first()
            if exists:
                errors.append(f"Строка {index+2}: код '{code}' уже существует")
                continue

            guest = Guest(code=code, name=name)
            db.add(guest)
            added_guests += 1
            logger.info(f"Added guest: {code} - {name}")

        except Exception as e:
            errors.append(f"Строка {index+2}: ошибка обработки - {str(e)}")
            logger.error(f"Error processing row {index}: {e}")
            continue

    try:
        db.commit()
        logger.info(f"Successfully committed {added_guests} guests to database")
    except Exception as e:
        db.rollback()
        logger.error(f"Database commit error: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных: {str(e)}")

    result = {
        "status": "ok",
        "added_guests": added_guests,
        "total_processed": total_rows,
        "errors_count": len(errors),
    }

    if errors:
        result["errors"] = errors[:10]
        logger.warning(f"Import completed with {len(errors)} errors")

    logger.info(f"Import completed: {added_guests} added, {len(errors)} errors")
    return result


@app.delete("/clear_all")
def clear_all(db: Session = Depends(get_db)):
    logger.warning("Clearing all database data")

    deleted_marks = db.query(Mark).delete()
    deleted_guests = db.query(Guest).delete()
    db.commit()

    logger.info(f"Database cleared: {deleted_guests} guests, {deleted_marks} marks deleted")
    return {
        "status": "ok",
        "deleted_guests": deleted_guests,
        "deleted_marks": deleted_marks,
    }


@app.post("/guests")
def add_guest(data: GuestCreate, db: Session = Depends(get_db)):
    code = (data.code or "").strip()
    name = data.name.strip()

    if not name:
        raise HTTPException(status_code=400, detail="Имя гостя не может быть пустым")

    if not code:
        code = f"NAME-{int(datetime.now().timestamp())}"

    logger.info(f"Adding guest: {code} - {name}")

    existing = db.query(Guest).filter(Guest.code == code).first()
    if existing:
        logger.warning(f"Guest already exists: {code}")
        raise HTTPException(status_code=400, detail="Гость с таким кодом уже существует")

    guest = Guest(code=code, name=name)
    db.add(guest)
    db.commit()
    db.refresh(guest)

    logger.info(f"Guest added successfully: {code}")
    return {
        "status": "ok",
        "message": "Гость добавлен",
        "guest": {"code": guest.code, "name": guest.name},
    }


@app.get("/guests")
def list_guests(db: Session = Depends(get_db)):
    guests = db.query(Guest).order_by(Guest.name.asc()).all()
    return [
        {"code": g.code, "name": g.name}
        for g in guests
    ]



@app.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    total_guests = db.query(Guest).count()
    total_scanned = db.query(Mark).count()

    return {
        "total_guests": total_guests,
        "total_scanned": total_scanned,
    }


@app.get("/search", response_model=List[SearchResult])
def search(query: str, db: Session = Depends(get_db)):
    q = query.strip()
    logger.info(f"Search request: '{q}'")

    if not q:
        raise HTTPException(status_code=400, detail="Пустой запрос")

    parts = [p for p in q.split() if p]
    if not parts:
        raise HTTPException(status_code=400, detail="Пустой запрос")

    guests = db.query(Guest).all()
    logger.info(f"Total guests in DB: {len(guests)}")

    norm_query = " ".join(q.lower().split())

    scored = []
    for g in guests:
        norm_name = " ".join(str(g.name).lower().split())
        dist = Levenshtein.distance(norm_query, norm_name)
        max_len = max(len(norm_query), len(norm_name)) or 1
        similarity = 1 - dist / max_len
        scored.append((g, similarity))

    threshold = 0.5
    filtered = [(g, s) for g, s in scored if s >= threshold]

    if not filtered:
        pattern = f"%{parts[0]}%"
        guests = db.query(Guest).filter(Guest.name.ilike(pattern)).all()
        logger.info(f"Fallback ilike found {len(guests)} guests")
        filtered = [(g, 1.0) for g in guests]

    filtered.sort(key=lambda x: x[1], reverse=True)

    all_marks = {mark.code: mark for mark in db.query(Mark).all()}

    results: List[SearchResult] = []
    for guest, sim in filtered[:50]:
        scanned = guest.code in all_marks
        results.append(
            SearchResult(
                code=guest.code,
                name=guest.name,
                scanned=scanned,
            )
        )

    logger.info(f"Returning {len(results)} search results")
    return results


@app.get("/export")
def export_data(db: Session = Depends(get_db)):
    guests = db.query(Guest).all()
    marks = db.query(Mark).all()

    csv_output = StringIO()
    writer = csv.writer(csv_output)
    writer.writerow(["Код", "ФИО", "Статус", "Время отметки", "Метод", "Источник"])

    marks_by_code = {m.code: m for m in marks}

    for guest in guests:
        mark = marks_by_code.get(guest.code)
        if mark:
            status = "Отмечен"
            timestamp = mark.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            method = mark.method
            source = ""
        else:
            status = "Не отмечен"
            timestamp = ""
            method = ""
            source = "Гость добавлен"

        writer.writerow([guest.code, guest.name, status, timestamp, method, source])

    csv_content = csv_output.getvalue()
    csv_output.close()

    stats = get_stats(db)

    txt_lines = [
        "СТАТИСТИКА СИСТЕМЫ ОТМЕТКИ",
        "",
        f"🎭 Всего гостей: {stats['total_guests']}",
        f"✅ Всего отсканировано: {stats['total_scanned']}",
        f"⏳ Ожидают: {stats['total_guests'] - stats['total_scanned']}",
        "",
        "Экспорт создан: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ]

    txt_content = "\n".join(txt_lines)

    return {
        "csv": csv_content,
        "txt": txt_content,
        "stats": stats,
    }


@app.post("/tg_users")
def add_telegram_user(data: TelegramUserCreate, db: Session = Depends(get_db)):
    logger.info(
        f"Adding Telegram user: id={data.telegram_id} username={data.username} name={data.name} allowed={data.allowed}"
    )

    q = db.query(TelegramUser)
    if data.telegram_id is not None:
        q = q.filter(TelegramUser.telegram_id == data.telegram_id)
    elif data.username:
        q = q.filter(TelegramUser.username == data.username)
    existing = q.first()

    if existing:
        existing.name = data.name
        if data.telegram_id is not None:
            existing.telegram_id = data.telegram_id
        if data.username:
            existing.username = data.username
        existing.allowed = data.allowed
        db.commit()
        db.refresh(existing)
        logger.info(f"Telegram user updated: id={existing.telegram_id} username={existing.username}")
        return {"status": "ok", "message": "Пользователь обновлён"}

    user = TelegramUser(
        telegram_id=data.telegram_id,
        username=data.username,
        name=data.name,
        allowed=data.allowed,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info(f"Telegram user added: id={user.telegram_id} username={user.username}")
    return {"status": "ok", "message": "Пользователь добавлен"}


@app.get("/tg_users")
def list_telegram_users(db: Session = Depends(get_db)):
    users = db.query(TelegramUser).all()
    return [
        {
            "telegram_id": u.telegram_id,
            "username": u.username,
            "name": u.name,
            "allowed": u.allowed,
        }
        for u in users
    ]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
