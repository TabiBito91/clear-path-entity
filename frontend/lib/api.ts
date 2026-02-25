const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function createSearch(
  name: string,
  entityType: string,
  states?: string[]
): Promise<{ job_id: string; states_queued: string[] }> {
  const res = await fetch(`${API}/api/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, entity_type: entityType, states }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? "Search request failed");
  }
  return res.json();
}

export function streamResults(jobId: string) {
  return new EventSource(`${API}/api/jobs/${jobId}/stream`);
}

export async function fetchEntityDetail(
  stateCode: string,
  fileNumber: string
): Promise<import("./types").EntityDetail> {
  const res = await fetch(`${API}/api/entity/${stateCode.toUpperCase()}/${encodeURIComponent(fileNumber)}`);
  if (!res.ok) throw new Error("Failed to fetch entity detail");
  return res.json();
}
