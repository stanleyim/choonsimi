export default async function handler(req, res) {
  try {
    // 🔥 캐시 (Vercel CDN)
    res.setHeader("Cache-Control", "s-maxage=86400, stale-while-revalidate");

    // 🔥 CORS (모바일/외부 접근 대비)
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET");

    // 🔥 현재 도메인 기준 data.json fetch
    const url = `https://${req.headers.host}/data.json`;

    const response = await fetch(url);
    if (!response.ok) throw new Error("data.json fetch fail");

    const data = await response.json();

    return res.status(200).json(data);

  } catch (e) {
    console.error("[API ERROR]", e);

    // 🔥 fallback (앱 안 죽게)
    return res.status(200).json({
      generated_at: new Date().toISOString(),
      top10: [],
      all: []
    });
  }
}
