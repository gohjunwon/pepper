// 주식 사이트 알람 — Cloudflare Worker
// 10분마다 깨어나서, 시각에 맞는 GitHub 저장소들에 "지금 실행" 신호를 보냅니다.
// 필요한 설정: 비밀값 GH_TOKEN (GitHub 토큰, Actions 읽기·쓰기 권한), 트리거(Cron) "*/10 * * * *"
// 점검: 이 Worker 주소를 브라우저로 열면 토큰과 저장소 연결 상태를 보여 줍니다(실행 신호는 보내지 않음).

const OWNER = "gohjunwon";

// 30분마다(매시 0분·30분) 보내는 것
const EVERY_30 = [
  { repo: "pepper", wf: "update.yml", inputs: { mode: "all" } },
  { repo: "k-stock-radar", wf: "radar.yml", inputs: { mode: "news" } },
  { repo: "sp500-dip", wf: "update.yml", inputs: { mode: "live" } },
  { repo: "swing", wf: "update.yml", inputs: { mode: "live" } },
  { repo: "alpha13f", wf: "auto_update.yml", inputs: {} },
];

// 10분마다 더 보내는 것 (그 사이 10·20·40·50분)
function extra10(t) {
  const h = t.getUTCHours(), d = t.getUTCDay(); // d: 0=일 … 6=토 (UTC)
  const out = [];
  // Pepper: 미국장 시작 1시간여 전 ~ 개장 직후 (UTC 12~14시, 월~금) → 오늘의 호재만
  if (d >= 1 && d <= 5 && h >= 12 && h <= 14) out.push({ repo: "pepper", wf: "update.yml", inputs: { mode: "today" } });
  // 국내주식: 한국 평일 08:00~15:50 = UTC 일~목 23시, 월~금 0~6시
  if ((h === 23 && d >= 0 && d <= 4) || (h <= 6 && d >= 1 && d <= 5))
    out.push({ repo: "k-stock-radar", wf: "radar.yml", inputs: { mode: "news" } });
  return out;
}

function jobsAt(t) {
  const m = t.getUTCMinutes();
  return m % 30 < 5 ? EVERY_30 : extra10(t);
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
  return `${j.repo}: ${r.status === 204 ? "실행 신호 보냄" : "실패 " + r.status + " " + (await r.text()).slice(0, 120)}`;
}

export default {
  async scheduled(event, env, ctx) {
    const t = new Date(event.scheduledTime);
    const res = await Promise.all(jobsAt(t).map((j) => dispatch(env, j)));
    console.log(t.toISOString(), res.join(" | "));
  },

  // 점검 화면
  async fetch(req, env) {
    const lines = [];
    if (!env.GH_TOKEN) lines.push("❌ 비밀값 GH_TOKEN 이 없습니다.");
    else {
      const seen = new Set();
      for (const j of [...EVERY_30]) {
        if (seen.has(j.repo)) continue;
        seen.add(j.repo);
        const r = await gh(env, `/repos/${OWNER}/${j.repo}/actions/workflows/${j.wf}`);
        lines.push(`${r.ok ? "✅" : "❌"} ${j.repo} (${j.wf}) ${r.ok ? "연결됨" : "연결 안 됨 " + r.status}`);
      }
    }
    const now = new Date();
    lines.push("", "지금 시각(UTC) " + now.toISOString().slice(0, 16));
    return new Response(lines.join("\n"), { headers: { "content-type": "text/plain; charset=utf-8" } });
  },
};
