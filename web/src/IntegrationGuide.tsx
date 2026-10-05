import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function IntegrationGuide() {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    setText("");

    async function load() {
      try {
        const response = await fetch("/api/integrations/guide", {
          signal: controller.signal,
        });

        if (!response.ok) throw new Error("load_failed");
        const content = await response.text();

        if (!controller.signal.aborted) setText(content);
      } catch {
        if (!controller.signal.aborted)
          setError(
            "지침을 불러오지 못했습니다. 로그인·승인 상태와 연결을 확인해 주세요.",
          );
      }
    }

    void load();

    return () => controller.abort();
  }, [attempt]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setError("복사하지 못했습니다. Markdown 다운로드를 사용해 주세요.");
    }
  }

  return (
    <section className="integration-guide" aria-label="서비스 연동 지침">
      <div className="guide-actions">
        <button disabled={!text} onClick={() => void copy()}>
          {copied ? "복사 완료" : "지침 전체 복사"}
        </button>
        <a className="button" href="/api/integrations/guide" download>
          Markdown 다운로드
        </a>
      </div>
      {error && (
        <p role="alert">
          {error}{" "}
          <button onClick={() => setAttempt(attempt + 1)}>다시 불러오기</button>
        </p>
      )}
      {!text && !error && <p role="status">연동 지침을 불러오고 있습니다…</p>}
      <article className="guide-document">
        <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
      </article>
    </section>
  );
}
