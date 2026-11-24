import json
from datetime import datetime, timezone
from edgar import Company, set_identity

# SEC EDGAR 규정에 따른 User-Agent 식별자 설정 (본인 이메일로 변경 권장)
set_identity("Alpha13F_Tracker admin@alpha13f-tracker.io")

# 월가 TOP 20 투자자 메타데이터 및 SEC 공식 CIK 번호
INVESTORS_META = [
    {"id": "buffett", "cik": "0001067983", "name": "워런 버핏", "enName": "Warren Buffett", "firm": "Berkshire Hathaway", "style": "value", "styleLabel": "가치투자", "philosophy": "경제적 해자를 가진 우량 기업의 장기 보유 및 현금흐름 중시"},
    {"id": "dalio", "cik": "0001350694", "name": "레이 달리오", "enName": "Ray Dalio", "firm": "Bridgewater Associates", "style": "macro", "styleLabel": "매크로/헤지", "philosophy": "올웨더(All Weather) 리스크 패리티 및 글로벌 거시경제 분산"},
    {"id": "ackman", "cik": "0001336528", "name": "빌 애크먼", "enName": "Bill Ackman", "firm": "Pershing Square Capital", "style": "activist", "styleLabel": "행동주의", "philosophy": "소수 우량 기업에 대한 초집중 투자 및 지배구조 개선"},
    {"id": "burry", "cik": "0001649339", "name": "마이클 버리", "enName": "Michael Burry", "firm": "Scion Asset Management", "style": "value", "styleLabel": "가치투자", "philosophy": "극단적 저평가 역발상 가치투자 및 거시적 리스크 헷징"},
    {"id": "druckenmiller", "cik": "0001536411", "name": "스탠리 드러켄밀러", "enName": "Stanley Druckenmiller", "firm": "Duquesne Family Office", "style": "macro", "styleLabel": "매크로/헤지", "philosophy": "유동성 사이클과 AI·기술 패러다임 변화에 따른 유연한 베팅"},
    {"id": "wood", "cik": "0001697748", "name": "캐시 우드", "enName": "Cathie Wood", "firm": "ARK Investment Management", "style": "growth", "styleLabel": "성장/혁신", "philosophy": "AI, 유전체학, 로보틱스, 블록체인 등 파괴적 혁신 기업 집중"},
    {"id": "griffin", "cik": "0001423053", "name": "켄 그리핀", "enName": "Ken Griffin", "firm": "Citadel Advisors", "style": "quant", "styleLabel": "퀀트", "philosophy": "멀티 전략 퀀트 트레이딩, 시장 조성 및 초단기 알파 추구"},
    {"id": "simons", "cik": "0001037389", "name": "짐 사이먼스", "enName": "Jim Simons", "firm": "Renaissance Technologies", "style": "quant", "styleLabel": "퀀트", "philosophy": "수학적 통계 모델과 알고리즘을 활용한 비선형 시장 패턴 포착"},
    {"id": "tepper", "cik": "0001656456", "name": "데이비드 테퍼", "enName": "David Tepper", "firm": "Appaloosa Management", "style": "macro", "styleLabel": "매크로/헤지", "philosophy": "부실 자산 및 거시적 정책 전환기 고수익 기회 포착"},
    {"id": "coleman", "cik": "0001167483", "name": "체이스 콜먼", "enName": "Chase Coleman", "firm": "Tiger Global Management", "style": "growth", "styleLabel": "성장/혁신", "philosophy": "글로벌 인터넷·소프트웨어·AI 생태계 승자 독식 기업 장기 투자"},
    {"id": "loeb", "cik": "0001040273", "name": "댄 롭", "enName": "Daniel Loeb", "firm": "Third Point", "style": "activist", "styleLabel": "행동주의", "philosophy": "이벤트 드리븐 및 행동주의적 개입을 통한 기업 가치 제고"},
    {"id": "icahn", "cik": "0000921669", "name": "칼 아이칸", "enName": "Carl Icahn", "firm": "Icahn Capital Management", "style": "activist", "styleLabel": "행동주의", "philosophy": "저평가된 자산주 경영권 압박 및 자사주 매입·배당 확대 요구"},
    {"id": "klarman", "cik": "0001061768", "name": "세스 클라만", "enName": "Seth Klarman", "firm": "Baupost Group", "style": "value", "styleLabel": "가치투자", "philosophy": "안전마진(Margin of Safety) 극대화 및 하방 리스크 철저 방어"},
    {"id": "marks", "cik": "0000949509", "name": "하워드 막스", "enName": "Howard Marks", "firm": "Oaktree Capital Management", "style": "value", "styleLabel": "가치투자", "philosophy": "시장 사이클 이해와 부실채권·고수익 크레딧 중심의 방어적 투자"},
    {"id": "li_lu", "cik": "0001709323", "name": "리 루", "enName": "Li Lu", "firm": "Himalaya Capital Management", "style": "value", "styleLabel": "가치투자", "philosophy": "찰리 멍거의 파트너, 소수 초우량 가치주 장기 집중 보유"},
    {"id": "pabrai", "cik": "0001549575", "name": "모니시 파브라이", "enName": "Mohnish Pabrai", "firm": "Dalal Street", "style": "value", "styleLabel": "가치투자", "philosophy": "단순하고 예측 가능한 비즈니스의 복제(Cloning) 및 집중 투자"},
    {"id": "greenblatt", "cik": "0001510387", "name": "조엘 그린블라트", "enName": "Joel Greenblatt", "firm": "Gotham Asset Management", "style": "quant", "styleLabel": "퀀트", "philosophy": "마법공식(높은 자본수익률 + 높은 이익수익률) 기반 롱숏 전략"},
    {"id": "singer", "cik": "0001048445", "name": "폴 싱어", "enName": "Paul Singer", "firm": "Elliott Investment Management", "style": "activist", "styleLabel": "행동주의", "philosophy": "글로벌 행동주의 캠페인, 구조조정 및 테크 기업 가치 극대화"},
    {"id": "einhorn", "cik": "0001079114", "name": "데이비드 아인혼", "enName": "David Einhorn", "firm": "Greenlight Capital", "style": "value", "styleLabel": "가치투자", "philosophy": "거시경제 인플레이션 수혜주 및 현금흐름 대비 저평가 종목 매수"},
    {"id": "laffont", "cik": "0001135730", "name": "필립 라폰트", "enName": "Philippe Laffont", "firm": "Coatue Management", "style": "growth", "styleLabel": "성장/혁신", "philosophy": "데이터 사이언스 기반의 글로벌 테크·AI·클라우드 성장주 투자"}
]

def fetch_all_13f():
    results = []
    for meta in INVESTORS_META:
        try:
            print(f"[{meta['enName']}] SEC 13F 공시 조회 중...")
            company = Company(meta["cik"])
            filings = company.get_filings(form="13F-HR")
            if not filings or len(filings) == 0:
                continue

            latest_filing = filings[0]
            thirteen_f = latest_filing.obj()
            df = thirteen_f.infotable

            if df is None or df.empty:
                continue

            # 동일 종목(CUSIP/Ticker) 합산 처리 (옵션/다중 계좌 통합)
            df["Value"] = df["Value"].astype(float)
            df["Shares"] = df["Shares"].astype(float)
            
            # SEC 13F Value 단위 보정 (천 달러 단위인 경우 달러로 환산)
            total_raw = df["Value"].sum()
            multiplier = 1000 if total_raw < 50_000_000 else 1
            df["ValueUSD"] = df["Value"] * multiplier

            grouped = df.groupby(["Issuer", "Ticker"], dropna=False).agg({
                "ValueUSD": "sum",
                "Shares": "sum"
            }).reset_index().sort_values(by="ValueUSD", ascending=False)

            total_aum_usd = grouped["ValueUSD"].sum()
            top_holdings = []

            for _, row in grouped.head(15).iterrows():
                ticker = str(row["Ticker"]) if str(row["Ticker"]) not in ["None", "nan", ""] else str(row["Issuer"])[:6].upper()
                val_usd = float(row["ValueUSD"])
                weight = round((val_usd / total_aum_usd) * 100, 2) if total_aum_usd > 0 else 0
                top_holdings.append({
                    "ticker": ticker,
                    "name": str(row["Issuer"]).title(),
                    "weight": weight,
                    "valueB": round(val_usd / 1e9, 3),
                    "shares": int(row["Shares"])
                })

            top5_weight = round(sum(h["weight"] for h in top_holdings[:5]), 1)

            results.append({
                "id": meta["id"],
                "cik": meta["cik"],
                "name": meta["name"],
                "enName": meta["enName"],
                "firm": meta["firm"],
                "style": meta["style"],
                "styleLabel": meta["styleLabel"],
                "philosophy": meta["philosophy"],
                "filingDate": str(latest_filing.filing_date),
                "reportPeriod": str(latest_filing.period_of_report),
                "aumB": round(total_aum_usd / 1e9, 2),
                "holdingsCount": len(grouped),
                "top5Weight": top5_weight,
                "holdings": top_holdings
            })
        except Exception as e:
            print(f"Error fetching {meta['enName']}: {e}")

    output = {
        "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "investors": results
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("data.json 업데이트 완료!")

if __name__ == "__main__":
    fetch_all_13f()
