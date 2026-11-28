// 주식 사이트 알람 — Cloudflare Worker
// 5분마다 깨어나서, 시각에 맞는 GitHub 저장소들에 "지금 실행" 신호를 보냅니다.
// 장 시작 1시간 전(미국장 사이트 = 뉴욕 8:30, 국내 = 한국 07:00)에는 반드시 갱신되도록,
// 신호를 보낸 뒤 5·10·15분 뒤에 실제로 실행됐는지 확인하고 안 됐으면 다시 보냅니다.
// 필요한 설정: 비밀값 GH_TOKEN (GitHub 토큰, Actions 읽기·쓰기 권한), 트리거(Cron) "*/5 * * * *"
// 점검: 이 Worker 주소를 브라우저로 열면 토큰·저장소 연결 상태와 저장소별 마지막 실행 시각을 보여 줍니다(실행 신호는 보내지 않음).

const OWNER = "gohjunwon";

// 미국 증시(NYSE) 휴장일 — 휴장일에는 개장 전 집중 갱신을 하지 않습니다
const US_HOLIDAYS = new Set([
  "2026-11-26", "2026-12-25",
  "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
  "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
]);

// 한국 증시(KRX) 휴장일 중 평일인 날(확실한 날만 넣음 — 틀리게 넣으면 필요한 갱신을 놓치므로)
const KR_HOLIDAYS = new Set([
  "2026-12-25", "2026-12-31",
  "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01", "2027-05-05", "2027-05-13", "2027-08-16",
  "2027-09-14", "2027-09-15", "2027-09-16", "2027-10-04", "2027-10-11", "2027-12-27", "2027-12-31",
]);

// 장 시작 1시간 전 '반드시 갱신' 대상 — T-60에 보내고, T-55·T-50·T-45에 실행을 확인해 없으면 다시 보냄(최대 3번)
const PRE_OPEN_US = [ // 뉴욕 8:30 ET (서머타임 자동, 미국 휴장일 제외)
  { repo: "pepper", wf: "update.yml", inputs: { mode: "today" } },
  { repo: "sp500-dip", wf: "update.yml", inputs: { mode: "live" } },
  { repo: "swing", wf: "update.yml", inputs: { mode: "live" } },
];
const PRE_OPEN_KR = [ // 한국 07:00 KST (평일, 한국 휴장일 제외)
  { repo: "k-stock-radar", wf: "radar.yml", inputs: { mode: "news" } },
];

// 30분마다(매시 0분·30분) 보내는 것
const EVERY_30 = [
  { repo: "pepper", wf: "update.yml", inputs: { mode: "today" } }, // 오늘의 호재·시간외 호재(가벼운 갱신)
  { repo: "k-stock-radar", wf: "radar.yml", inputs: { mode: "news" } },
  { repo: "sp500-dip", wf: "update.yml", inputs: { mode: "live" } },
  { repo: "swing", wf: "update.yml", inputs: { mode: "live" } },
];
// value-rise(10년 분석, 주 1회)는 자체 예약으로만 돕니다.

// 점검 화면에 보여 줄 저장소들
const WATCH = [
  ...EVERY_30,
  { repo: "alpha13f", wf: "auto_update.yml" },
  { repo: "value-rise", wf: "update.yml" },
];

// 뉴욕 현지 시각(서머타임 자동 반영)
function nyParts(t) {
  const f = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", weekday: "short", hourCycle: "h23",
  });
  const p = Object.fromEntries(f.formatToParts(t).map((x) => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, h: +p.hour, m: +p.minute, wd: p.weekday };
}

function usTradingDay(ny) {
  return !["Sat", "Sun"].includes(ny.wd) && !US_HOLIDAYS.has(ny.date);
}

function krParts(t) {
  const k = new Date(t.getTime() + 9 * 3600e3); // 한국은 서머타임 없음
  return { date: k.toISOString().slice(0, 10), h: k.getUTCHours(), m: k.getUTCMinutes(), wd: k.getUTCDay() };
}

// 지금이 '장 시작 1시간 전' 보장 구간이면 {jobs, since(T-60 시각), check(확인 회차인지)}
function preOpen(t) {
  const out = [];
  const ny = nyParts(t);
  if (usTradingDay(ny)) {
    const diff = ny.h * 60 + ny.m - (8 * 60 + 30); // 0 = T-60
    if ([0, 5, 10, 15].includes(diff)) out.push({ jobs: PRE_OPEN_US, since: new Date(t.getTime() - diff * 60e3), check: diff > 0 });
  }
  const kr = krParts(t);
  if (kr.wd >= 1 && kr.wd <= 5 && !KR_HOLIDAYS.has(kr.date)) {
    const diff = kr.h * 60 + kr.m - 7 * 60;
    if ([0, 5, 10, 15].includes(diff)) out.push({ jobs: PRE_OPEN_KR, since: new Date(t.getTime() - diff * 60e3), check: diff > 0 });
  }
  return out;
}

// since 이후 시작된 실행 중 성공했거나 아직 도는 것이 있으면 true
async function ranSince(env, j, since) {
  const r = await gh(env, `/repos/${OWNER}/${j.repo}/actions/workflows/${j.wf}/runs?per_page=10`);
  if (!r.ok) return false;
  const runs = (await r.json()).workflow_runs || [];
  return runs.some((x) => new Date(x.created_at) >= since &&
    (x.conclusion === "success" || ["queued", "in_progress", "waiting", "pending", "requested"].includes(x.status)));
}

function jobsAt(t) {
  const m = t.getUTCMinutes(), h = t.getUTCHours(), d = t.getUTCDay(); // d: 0=일 … 6=토 (UTC)
  const mon = t.getUTCMonth() + 1, day = t.getUTCDate();
  const out = [];
  const on30 = m === 0 || m === 30;
  if (on30) out.push(...EVERY_30);

  // Pepper 전체 갱신(1달 일정·미리보기·캘린더): 한국 06:40 = UTC 21:40, 한국 20:40 = UTC 11:40
  if (m === 40 && (h === 21 || h === 11)) out.push({ repo: "pepper", wf: "update.yml", inputs: { mode: "all" } });

  // Pepper 개장 전후 집중 갱신: 개장(9:30 ET) 60·50·40·30·20·10·5분 전, 개장 시각, 5분 후
  const ny = nyParts(t);
  if (usTradingDay(ny)) {
    const diff = ny.h * 60 + ny.m - (9 * 60 + 30);
    if ([-60, -50, -40, -30, -20, -10, -5, 0, 5].includes(diff)) out.push({ repo: "pepper", wf: "update.yml", inputs: { mode: "today" } });
  }

  // 국내주식: 한국 평일 08:00~15:50은 10분마다 (UTC 일~목 23시, 월~금 0~6시)
  if (m % 10 === 0 && !on30 && ((h === 23 && d >= 0 && d <= 4) || (h <= 6 && d >= 1 && d <= 5)))
    out.push({ repo: "k-stock-radar", wf: "radar.yml", inputs: { mode: "news" } });

  // alpha13f(13F 분기 공시): 평소 하루 1번(한국 10:05), 공시 마감이 몰리는 2·5·8·11월 10~20일엔 3시간마다
  const season = [2, 5, 8, 11].includes(mon) && day >= 10 && day <= 20;
  if (m === 5 && (h === 1 || (season && h % 3 === 0))) out.push({ repo: "alpha13f", wf: "auto_update.yml", inputs: {} });

  // 장 시작 1시간 전(T-60) 필수 갱신
  for (const p of preOpen(t)) if (!p.check) out.push(...p.jobs);

  // 같은 저장소·같은 모드는 한 번만
  const seen = new Set();
  return out.filter((j) => {
    const k = j.repo + "|" + JSON.stringify(j.inputs);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

const gh = (env, path, init = {}) =>
  fetch("https://api.github.com" + path, {
    ...init,
    headers: {
      Authorization: "Bearer " + env.GH_TOKEN,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "stock-sites-alarm",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
    },
  });

async function dispatch(env, j) {
  const r = await gh(env, `/repos/${OWNER}/${j.repo}/actions/workflows/${j.wf}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: "main", inputs: j.inputs }),
  });
  return `${j.repo}(${j.inputs.mode || "-"})${j.retry ? "[재전송]" : ""}: ${r.status === 204 ? "실행 신호 보냄" : "실패 " + r.status + " " + (await r.text()).slice(0, 120)}`;
}

const kst = (iso) => new Date(new Date(iso).getTime() + 9 * 3600e3).toISOString().slice(5, 16).replace("T", " ");

export default {
  async scheduled(event, env, ctx) {
    const t = new Date(event.scheduledTime);
    const jobs = jobsAt(t);
    // 장 시작 1시간 전 확인 회차(T-55·T-50·T-45): T-60 이후 실행이 없으면 다시 보냄
    for (const p of preOpen(t)) {
      if (!p.check) continue;
      for (const j of p.jobs) {
        if (jobs.some((x) => x.repo === j.repo)) continue;
        if (!(await ranSince(env, j, p.since))) jobs.push({ ...j, retry: true });
      }
    }
    if (!jobs.length) return;
    const res = await Promise.all(jobs.map((j) => dispatch(env, j)));
    console.log(t.toISOString(), res.join(" | "));
  },

  // 점검 화면
  async fetch(req, env) {
    const lines = [];
    if (!env.GH_TOKEN) lines.push("❌ 비밀값 GH_TOKEN 이 없습니다.");
    else {
      const seen = new Set();
      for (const j of WATCH) {
        if (seen.has(j.repo)) continue;
        seen.add(j.repo);
        const r = await gh(env, `/repos/${OWNER}/${j.repo}/actions/workflows/${j.wf}/runs?per_page=1`);
        if (!r.ok) { lines.push(`❌ ${j.repo} (${j.wf}) 연결 안 됨 ${r.status}`); continue; }
        const run = ((await r.json()).workflow_runs || [])[0];
        const last = run ? `마지막 실행 ${kst(run.created_at)}(한국) · ${run.event} · ${run.conclusion || run.status}` : "실행 기록 없음";
        lines.push(`✅ ${j.repo} (${j.wf}) 연결됨 · ${last}`);
      }
    }
    const now = new Date();
    lines.push("", "지금 시각(한국) " + kst(now.toISOString()));
    const next = [];
    for (let k = 1; k <= 12 && next.length < 6; k++) {
      const t = new Date(Math.floor(now.getTime() / 300e3) * 300e3 + k * 300e3);
      const j = jobsAt(t);
      if (j.length) next.push(`${kst(t.toISOString())} → ${j.map((x) => x.repo + (x.inputs.mode ? "(" + x.inputs.mode + ")" : "")).join(", ")}`);
    }
    lines.push("다음 예정(1시간 안):", ...(next.length ? next : ["없음"]));
    return new Response(lines.join("\n"), { headers: { "content-type": "text/plain; charset=utf-8" } });
  },
};
