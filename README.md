# Pepper — 미국주식 호재 레이더

오늘의 호재, 1달 앞 일정, 실적 미리보기, 시장 일정을 한국 시각으로 보는 사이트입니다.

- **Claude 연결 없이** GitHub Actions(`.github/workflows/update.yml`)가 혼자 갱신합니다.
  - 미국장 시작 1시간여 전 ~ 개장 직후: 10분마다 오늘의 호재
  - 장 마감 뒤 ~ 다음 장 전: 매시간 시간외 호재
  - 하루 두 번(한국 아침 6시대·저녁 8시대): 1달 앞 일정·시장 일정·실적 미리보기·캘린더
- 자료: Yahoo Finance(주가·뉴스·애널리스트 전망), Nasdaq(실적·경제 달력), Google 뉴스, 무료 구글 번역
- 구글 캘린더 구독: `https://gohjunwon.github.io/pepper/data/calendar.ics`
- 비밀값(Settings → Secrets → Actions): `MY_PASS`(나만 보는 /my/ 사이트 비밀번호), `WATCHLIST`(관심종목, 예: `CPNG,TSLA`)

투자 권유가 아니며, 모든 정보는 공개된 기사·공시를 바탕으로 자동 수집·번역합니다.
