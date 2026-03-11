from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, EmailStr
from typing import Optional
import bcrypt
import pyotp
import qrcode
import qrcode.image.svg
import io
import base64
from jose import JWTError, jwt
from datetime import datetime, timedelta
import redis as redis_client

from app.config import settings
from app.database import get_db

router = APIRouter()
security = HTTPBearer()

# Redis bağlantısı
redis = redis_client.from_url(settings.REDIS_URL, decode_responses=True)


# ── Pydantic Modeller ──────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    sifre: str

class TOTPRequest(BaseModel):
    email: EmailStr
    totp_kodu: str
    gecici_token: str

class SifreDegistirRequest(BaseModel):
    eski_sifre: str
    yeni_sifre: str

class TOTPDogrulaRequest(BaseModel):
    totp_kodu: str

class KullaniciOlusturRequest(BaseModel):
    email: EmailStr
    ad: str
    soyad: str
    sifre: str
    rol: str = "user"


# ── JWT Yardımcı Fonksiyonlar ──────────────────────────────────

def token_olustur(data: dict, expire_minutes: int = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(
        minutes=expire_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def token_dogrula(token: str):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None


def mevcut_kullanici(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    token = credentials.credentials

    # Blacklist kontrolü
    if redis.get(f"blacklist:{token}"):
        raise HTTPException(status_code=401, detail="Token geçersiz")

    payload = token_dogrula(token)
    if not payload or payload.get("tip") != "access":
        raise HTTPException(status_code=401, detail="Geçersiz token")

    user = db.execute(
        text("SELECT * FROM arge_rag.users WHERE id = :id AND aktif = TRUE"),
        {"id": payload.get("user_id")}
    ).fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="Kullanıcı bulunamadı")

    return user


def admin_kullanici(user=Depends(mevcut_kullanici)):
    if user.rol != "admin":
        raise HTTPException(status_code=403, detail="Bu işlem için admin yetkisi gerekli")
    return user


# ── Endpoint'ler ───────────────────────────────────────────────

@router.post("/login")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """1. Adım: Email + şifre kontrolü"""

    user = db.execute(
        text("SELECT * FROM arge_rag.users WHERE email = :email AND aktif = TRUE"),
        {"email": request.email}
    ).fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="Email veya şifre hatalı")

    if not bcrypt.checkpw(request.sifre.encode("utf-8"), user.sifre_hash.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Email veya şifre hatalı")

    totp_secret = db.execute(
        text("SELECT * FROM arge_rag.totp_secrets WHERE user_id = :uid AND dogrulandin_mi = TRUE"),
        {"uid": user.id}
    ).fetchone()

    gecici_token = token_olustur(
        {"user_id": user.id, "email": user.email, "tip": "gecici"},
        expire_minutes=5
    )

    if not totp_secret:
        return {
            "durum": "2fa_kurulum_gerekli",
            "gecici_token": gecici_token,
            "mesaj": "Google Authenticator kurulumu gerekli"
        }

    return {
        "durum": "2fa_gerekli",
        "gecici_token": gecici_token,
        "mesaj": "Google Authenticator kodunu girin"
    }


@router.post("/2fa/kurulum")
async def totp_kurulum(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """2FA QR kodu oluştur"""

    token = credentials.credentials
    payload = token_dogrula(token)

    if not payload or payload.get("tip") != "gecici":
        raise HTTPException(status_code=401, detail="Geçersiz token")

    user_id = payload.get("user_id")
    email = payload.get("email")

    db.execute(
        text("DELETE FROM arge_rag.totp_secrets WHERE user_id = :uid"),
        {"uid": user_id}
    )

    secret = pyotp.random_base32()

    db.execute(
        text("INSERT INTO arge_rag.totp_secrets (user_id, secret_key, dogrulandin_mi) VALUES (:uid, :secret, FALSE)"),
        {"uid": user_id, "secret": secret}
    )
    db.commit()

    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=email, issuer_name="Antares ArGe RAG")

    qr = qrcode.make(uri)
    buffer = io.BytesIO()
    qr.save(buffer)
    qr_base64 = base64.b64encode(buffer.getvalue()).decode()

    return {
        "qr_kodu": f"data:image/png;base64,{qr_base64}",
        "manuel_kod": secret,
        "mesaj": "QR kodu Google Authenticator ile tarayın"
    }


@router.post("/2fa/dogrula")
async def totp_dogrula(
    request: TOTPDogrulaRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """2FA kurulumunu doğrula ve access token ver"""

    token = credentials.credentials
    payload = token_dogrula(token)

    if not payload or payload.get("tip") != "gecici":
        raise HTTPException(status_code=401, detail="Geçersiz token")

    user_id = payload.get("user_id")

    totp_secret = db.execute(
        text("SELECT * FROM arge_rag.totp_secrets WHERE user_id = :uid"),
        {"uid": user_id}
    ).fetchone()

    if not totp_secret:
        raise HTTPException(status_code=400, detail="2FA kurulumu bulunamadı")

    totp = pyotp.TOTP(totp_secret.secret_key)
    if not totp.verify(request.totp_kodu, valid_window=1):
        raise HTTPException(status_code=401, detail="Geçersiz 2FA kodu")

    db.execute(
        text("UPDATE arge_rag.totp_secrets SET dogrulandin_mi = TRUE WHERE user_id = :uid"),
        {"uid": user_id}
    )
    db.commit()

    access_token = token_olustur({"user_id": user_id, "tip": "access"})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "mesaj": "Giriş başarılı"
    }


@router.post("/2fa/giris")
async def totp_giris(request: TOTPRequest, db: Session = Depends(get_db)):
    """2FA ile giriş (kurulum tamamlanmış kullanıcılar için)"""

    payload = token_dogrula(request.gecici_token)
    if not payload or payload.get("tip") != "gecici":
        raise HTTPException(status_code=401, detail="Geçersiz veya süresi dolmuş token")

    user_id = payload.get("user_id")

    totp_secret = db.execute(
        text("SELECT * FROM arge_rag.totp_secrets WHERE user_id = :uid AND dogrulandin_mi = TRUE"),
        {"uid": user_id}
    ).fetchone()

    if not totp_secret:
        raise HTTPException(status_code=400, detail="2FA kurulumu bulunamadı")

    totp = pyotp.TOTP(totp_secret.secret_key)
    if not totp.verify(request.totp_kodu, valid_window=1):
        raise HTTPException(status_code=401, detail="Geçersiz 2FA kodu")

    access_token = token_olustur({"user_id": user_id, "tip": "access"})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "mesaj": "Giriş başarılı"
    }


@router.post("/logout")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Çıkış yap - token'ı blacklist'e ekle"""
    token = credentials.credentials
    payload = token_dogrula(token)

    if payload:
        exp = payload.get("exp")
        now = datetime.utcnow().timestamp()
        ttl = max(int(exp - now), 1)
        redis.setex(f"blacklist:{token}", ttl, "1")

    return {"mesaj": "Çıkış başarılı"}


@router.post("/sifre-degistir")
async def sifre_degistir(
    request: SifreDegistirRequest,
    user=Depends(mevcut_kullanici),
    db: Session = Depends(get_db)
):
    """Şifre değiştir"""

    if not bcrypt.checkpw(request.eski_sifre.encode("utf-8"), user.sifre_hash.encode("utf-8")):
        raise HTTPException(status_code=400, detail="Mevcut şifre hatalı")

    yeni_hash = bcrypt.hashpw(request.yeni_sifre.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    db.execute(
        text("UPDATE arge_rag.users SET sifre_hash = :hash, guncelleme_tarihi = NOW() WHERE id = :id"),
        {"hash": yeni_hash, "id": user.id}
    )
    db.commit()

    return {"mesaj": "Şifre başarıyla değiştirildi"}


@router.get("/ben")
async def ben(user=Depends(mevcut_kullanici)):
    """Mevcut kullanıcı bilgileri"""
    return {
        "id": user.id,
        "email": user.email,
        "ad": user.ad,
        "soyad": user.soyad,
        "rol": user.rol
    }


# ── Admin Endpoint'leri ────────────────────────────────────────

@router.post("/admin/kullanici-olustur")
async def kullanici_olustur(
    request: KullaniciOlusturRequest,
    admin=Depends(admin_kullanici),
    db: Session = Depends(get_db)
):
    """Admin: Yeni kullanıcı oluştur"""

    mevcut = db.execute(
        text("SELECT id FROM arge_rag.users WHERE email = :email"),
        {"email": request.email}
    ).fetchone()

    if mevcut:
        raise HTTPException(status_code=400, detail="Bu email zaten kayıtlı")

    sifre_hash = bcrypt.hashpw(request.sifre.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    db.execute(
        text("""
            INSERT INTO arge_rag.users (email, ad, soyad, sifre_hash, rol, aktif, ilk_giris)
            VALUES (:email, :ad, :soyad, :hash, :rol, TRUE, TRUE)
        """),
        {
            "email": request.email,
            "ad": request.ad,
            "soyad": request.soyad,
            "hash": sifre_hash,
            "rol": request.rol
        }
    )
    db.commit()

    return {"mesaj": f"{request.email} kullanıcısı oluşturuldu"}


@router.get("/admin/kullanicilar")
async def kullanicilari_listele(
    admin=Depends(admin_kullanici),
    db: Session = Depends(get_db)
):
    """Admin: Tüm kullanıcıları listele"""

    users = db.execute(
        text("SELECT id, email, ad, soyad, rol, aktif, olusturma_tarihi FROM arge_rag.users ORDER BY id")
    ).fetchall()

    return [
        {
            "id": u.id,
            "email": u.email,
            "ad": u.ad,
            "soyad": u.soyad,
            "rol": u.rol,
            "aktif": u.aktif,
            "olusturma_tarihi": u.olusturma_tarihi
        }
        for u in users
    ]


@router.patch("/admin/kullanici/{user_id}/aktif")
async def kullanici_aktif_toggle(
    user_id: int,
    admin=Depends(admin_kullanici),
    db: Session = Depends(get_db)
):
    """Admin: Kullanıcıyı aktif/pasif yap"""

    user = db.execute(
        text("SELECT id, aktif, email FROM arge_rag.users WHERE id = :id"),
        {"id": user_id}
    ).fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")

    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Kendi hesabınızı pasif yapamazsınız")

    yeni_durum = not user.aktif
    db.execute(
        text("UPDATE arge_rag.users SET aktif = :aktif WHERE id = :id"),
        {"aktif": yeni_durum, "id": user_id}
    )
    db.commit()

    return {"mesaj": f"Kullanıcı {'aktif' if yeni_durum else 'pasif'} yapıldı"}


@router.delete("/admin/kullanici/{user_id}")
async def kullanici_sil(
    user_id: int,
    admin=Depends(admin_kullanici),
    db: Session = Depends(get_db)
):
    """Admin: Kullanıcıyı sil"""

    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Kendi hesabınızı silemezsiniz")

    db.execute(text("DELETE FROM arge_rag.users WHERE id = :id"), {"id": user_id})
    db.commit()

    return {"mesaj": "Kullanıcı silindi"}