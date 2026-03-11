from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import test_connection
from app.routers import auth, query

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://antares.artesantr.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(query.router, prefix="/api/query", tags=["query"])


@app.on_event("startup")
async def startup():
    if test_connection():
        print("✅ Veritabanı bağlantısı başarılı")
    else:
        print("❌ Veritabanı bağlantısı başarısız")


@app.get("/")
async def root():
    return {"mesaj": "Antares ArGe RAG API çalışıyor", "versiyon": settings.APP_VERSION}


@app.get("/health")
async def health():
    db_ok = test_connection()
    return {
        "durum": "ok" if db_ok else "hata",
        "veritabani": "bağlı" if db_ok else "bağlantı yok"
    }