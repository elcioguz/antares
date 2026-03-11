import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import io, json, re
from pathlib import Path
import pdfplumber
import sys

# ===============================
# UNIVERSAL SCRAPER v4.1
# ===============================
# Özellikler:
# ✅ PDF ve HTML tam metin çıkarır (pdfplumber ile)
# ✅ Sayfa içi (#) linkleri eler
# ✅ Kullanıcıya subdomain (prefixli URL) seçimi sunar
# ✅ "Evet" seçildiğinde bilgilendirici mesaj gösterir
# ✅ Başlangıç URL altındaki bağlantılarla sınırlı çalışır

arg1 = sys.argv[1]  
arg2 = sys.argv[2]  

MAX_PAGES = 80
TIMEOUT = 20
visited = set()
results = []

def get_html(url):
    """Sayfa HTML'ini getirir"""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        if "text/html" in r.headers.get("Content-Type", ""):
            return r.text
        return None
    except Exception as e:
        print(f"[HATA] {url}: {e}", file=sys.stderr)
        return None

def extract_text_from_html(html):
    """HTML içinden görünen tüm metni çıkarır"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = " ".join(soup.stripped_strings)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def extract_text_from_pdf(url):
    """PDF indirip pdfplumber ile temiz metin çıkarır"""
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        with io.BytesIO(r.content) as bio, pdfplumber.open(bio) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n".join(pages)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception as e:
        print(f"[PDF Hatası] {url}: {e}", file=sys.stderr)
        return ""

def crawl(url, allowed_prefix, allowed_domain, include_subdomains, depth=0):
    """Sadece belirlenen alan adı (ve istenirse subdomain) altında gezin"""
    if url in visited or len(visited) > MAX_PAGES:
        return
    visited.add(url)
    # stderr'e yaz
    print(f"[{len(visited)}] Geziyor: {url}", file=sys.stderr)

    if url.lower().endswith(".pdf"):
        text = extract_text_from_pdf(url)
        results.append({
            "url": url,
            "type": "pdf",
            "text": text
        })
        return

    html = get_html(url)
    if not html:
        return

    text = extract_text_from_html(html)
    results.append({
        "url": url,
        "type": "html",
        "text": text
    })

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        link = urljoin(url, a["href"])

        if "#" in link:
            link = link.split("#")[0]

        link_domain = urlparse(link).netloc
        same_domain = (link_domain == allowed_domain)
        same_root = link_domain.endswith("." + allowed_domain)

        if (include_subdomains and (same_domain or same_root)) or (not include_subdomains and same_domain):
            if link.startswith(allowed_prefix) or include_subdomains:
                if link not in visited:
                    crawl(link, allowed_prefix, allowed_domain, include_subdomains, depth + 1)

# Skorlama fonksiyonu 
def score_content(text):
    score = 0
    lower_text = text.lower()
    
    if '5746' in lower_text: score += 10
    if '4691' in lower_text: score += 10
    if 'ar-ge' in lower_text: score += 10
    if 'tasarım' in lower_text: score += 4
    if 'yönetmelik' in lower_text: score += 5
    if 'tebliğ' in lower_text: score += 5
    if 'resmi gazete' in lower_text: score += 8
    if 'mevzuat' in lower_text: score += 4
    if 'değişiklik' in lower_text: score += 4
    if 'yeni' in lower_text: score += 2
    if 'duyuru' in lower_text: score += 3
    
    # Dinamik tarih kontrolü
    from datetime import datetime
    current_year = datetime.now().year
    last_year = current_year - 1
    
    if str(current_year) in lower_text: score += 5
    if str(last_year) in lower_text: score += 3
    
    return score

def main():
    start_url = sys.argv[1].strip()
    if not start_url.startswith("http"):
        print(json.dumps({"error": "Geçerli bir URL giriniz."}), file=sys.stderr)
        sys.exit(1)
    
    include_subdomains = sys.argv[2].lower().startswith("e")

    parsed = urlparse(start_url)
    allowed_prefix = start_url.rstrip("/")
    allowed_domain = parsed.netloc

    # Bilgilendirme mesajlarını stderr'e yaz (n8n logs'da görünür)
    if include_subdomains:
        root_domain_parts = allowed_domain.split(".")[-2:]
        root_domain = ".".join(root_domain_parts)
        print(f"→ Yalnızca *.{root_domain} altındaki sayfalar taranacak "
              f"({root_domain}.net, {root_domain}.org, {root_domain}.tr hariç)", 
              file=sys.stderr)
    else:
        print(f"→ Sadece {allowed_domain} üzerindeki sayfalar taranacak.", 
              file=sys.stderr)

    crawl(start_url, allowed_prefix, allowed_domain, include_subdomains)

    print(f"✅ {len(results)} sayfa işlendi.", file=sys.stderr)
    
    # Sadece ilgili sayfaları filtrele
    relevant_results = []
    for item in results:
        score = score_content(item['text'])
        if score > 0:
            relevant_results.append({
                'url': item['url'],
                'type': item['type'],
                'text': item['text'],
                'score': score
            })
    
    print(f"✅ {len(relevant_results)} ilgili sayfa bulundu (skor > 0).", file=sys.stderr)
    
    # Direkt stdout'a JSON yaz (n8n bunu alacak)
    print(json.dumps(relevant_results, ensure_ascii=False))

if __name__ == "__main__":
    main()