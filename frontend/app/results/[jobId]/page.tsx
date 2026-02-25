"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchEntityDetail, streamResults } from "@/lib/api";
import type { EntityDetail, StateResult, UsptoResult } from "@/lib/types";
import {
  CheckCircle,
  XCircle,
  AlertTriangle,
  HelpCircle,
  ArrowLeft,
  Loader2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function AvailabilityIcon({ status }: { status: string }) {
  switch (status) {
    case "available":
      return <CheckCircle className="h-5 w-5 text-green-500" />;
    case "taken":
      return <XCircle className="h-5 w-5 text-red-500" />;
    case "similar":
      return <AlertTriangle className="h-5 w-5 text-yellow-500" />;
    default:
      return <HelpCircle className="h-5 w-5 text-neutral-400" />;
  }
}

function AvailabilityBadge({ status }: { status: string }) {
  const variants: Record<string, string> = {
    available: "bg-green-50 text-green-700 border-green-200",
    taken: "bg-red-50 text-red-700 border-red-200",
    similar: "bg-yellow-50 text-yellow-700 border-yellow-200",
    unknown: "bg-neutral-100 text-neutral-600 border-neutral-200",
    error: "bg-neutral-100 text-neutral-600 border-neutral-200",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${variants[status] ?? variants.unknown}`}
    >
      {status}
    </span>
  );
}

function ConfidencePill({ value }: { value: number }) {
  const label = value >= 0.8 ? "High" : value >= 0.5 ? "Medium" : "Low";
  const color =
    value >= 0.8
      ? "text-green-600"
      : value >= 0.5
        ? "text-yellow-600"
        : "text-red-500";
  return (
    <span className={`text-xs font-medium ${color}`}>
      {label} confidence ({Math.round(value * 100)}%)
    </span>
  );
}

function RiskBadge({ level }: { level: string }) {
  const variants: Record<string, string> = {
    low: "bg-green-50 text-green-700 border-green-200",
    medium: "bg-yellow-50 text-yellow-700 border-yellow-200",
    high: "bg-red-50 text-red-700 border-red-200",
    unknown: "bg-neutral-100 text-neutral-500 border-neutral-200",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${variants[level] ?? variants.unknown}`}
    >
      {level} risk
    </span>
  );
}

// ---------------------------------------------------------------------------
// Per-match row with on-demand detail expand
// ---------------------------------------------------------------------------

function MatchRow({
  match,
  stateCode,
}: {
  match: Record<string, string>;
  stateCode: string;
}) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const fileNumber = match.file_number ?? "";
  const name = match.name ?? "";

  async function toggle() {
    if (!open && !detail && fileNumber) {
      setLoading(true);
      try {
        const d = await fetchEntityDetail(stateCode, fileNumber);
        setDetail(d);
      } catch {
        setDetail({ file_number: fileNumber, entity_name: null, entity_kind: null, formation_date: null, registered_agent: null, opencorporates_url: `https://opencorporates.com/companies/us_de/${fileNumber}`, error: "Could not load detail.", cached: false });
      } finally {
        setLoading(false);
      }
    }
    setOpen((v) => !v);
  }

  return (
    <li className="rounded border border-neutral-100 bg-neutral-50">
      <button
        className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left"
        onClick={toggle}
      >
        <span className="flex items-center gap-1.5 min-w-0">
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
          )}
          <span className="text-xs font-mono text-neutral-700 truncate">{name}</span>
        </span>
        {fileNumber && (
          <span className="text-[10px] text-neutral-400 shrink-0">#{fileNumber}</span>
        )}
      </button>

      {open && (
        <div className="px-3 pb-3 pt-0 border-t border-neutral-100">
          {loading ? (
            <div className="flex items-center gap-1.5 py-2 text-xs text-neutral-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Loading details...
            </div>
          ) : detail ? (
            <div className="mt-2 space-y-1 text-xs text-neutral-600">
              {detail.error && (
                <p className="text-red-500">{detail.error}</p>
              )}
              {detail.formation_date && (
                <p><span className="text-neutral-400">Formed:</span> {detail.formation_date}</p>
              )}
              {detail.entity_kind && (
                <p><span className="text-neutral-400">Kind:</span> {detail.entity_kind}</p>
              )}
              {detail.registered_agent && (
                <p><span className="text-neutral-400">Registered agent:</span> {detail.registered_agent}</p>
              )}
              <a
                href={detail.opencorporates_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-blue-500 hover:text-blue-700 mt-1"
              >
                OpenCorporates <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          ) : null}
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// State result card
// ---------------------------------------------------------------------------

function StateCard({ result }: { result: StateResult }) {
  const [matchesExpanded, setMatchesExpanded] = useState(false);
  const hasDetails = result.raw_matches.length > 0 || result.flags.length > 0 || result.notes;

  return (
    <Card className="border border-neutral-200 shadow-none">
      <CardHeader className="pb-2 pt-4 px-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <AvailabilityIcon status={result.availability} />
            <CardTitle className="text-sm font-medium text-neutral-800">
              {result.state_name}
            </CardTitle>
          </div>
          <div className="flex items-center gap-2">
            <AvailabilityBadge status={result.availability} />
            <ConfidencePill value={result.confidence} />
          </div>
        </div>
      </CardHeader>

      {hasDetails && (
        <CardContent className="px-4 pb-4">
          {result.notes && (
            <p className="text-xs text-neutral-500 mb-2">{result.notes}</p>
          )}

          {result.flags.length > 0 && (
            <div className="space-y-1 mb-2">
              {result.flags.map((f, i) => (
                <p key={i} className="text-xs text-amber-700 bg-amber-50 rounded px-2 py-1">
                  {f}
                </p>
              ))}
            </div>
          )}

          {result.raw_matches.length > 0 && (
            <>
              <button
                className="text-xs text-neutral-400 underline"
                onClick={() => setMatchesExpanded((v) => !v)}
              >
                {matchesExpanded ? "Hide" : "Show"} {result.raw_matches.length} matching registration(s)
              </button>
              {matchesExpanded && (
                <ul className="mt-2 space-y-1.5">
                  {result.raw_matches.map((m, i) => (
                    <MatchRow key={m.file_number ?? i} match={m} stateCode={result.state_code} />
                  ))}
                </ul>
              )}
            </>
          )}
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// USPTO card
// ---------------------------------------------------------------------------

function UsptoCard({ result }: { result: UsptoResult }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <Card className="border border-neutral-200 shadow-none">
      <CardHeader className="pb-2 pt-4 px-4">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm font-medium text-neutral-800">
            Federal Trademark (USPTO)
          </CardTitle>
          <RiskBadge level={result.risk_level} />
        </div>
      </CardHeader>
      <CardContent className="px-4 pb-4">
        <p className="text-xs text-neutral-500 mb-2">{result.notes}</p>

        {(result.exact_matches.length > 0 || result.similar_marks.length > 0) && (
          <>
            <button
              className="text-xs text-neutral-400 underline"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? "Hide" : "Show"} trademark details
            </button>
            {expanded && (
              <div className="mt-3 space-y-2">
                {result.exact_matches.map((m, i) => (
                  <div key={i} className="text-xs bg-red-50 rounded p-2">
                    <span className="font-medium">{m.mark}</span>{" "}
                    <Badge variant="outline" className="text-[10px]">
                      {m.live ? "LIVE" : "DEAD"}
                    </Badge>
                    <br />
                    <span className="text-neutral-500">
                      Owner: {m.owner} · Classes: {m.nice_classes.join(", ")}
                    </span>
                  </div>
                ))}
                {result.similar_marks.slice(0, 5).map((m, i) => (
                  <div key={i} className="text-xs bg-neutral-50 rounded p-2">
                    <span className="font-medium">{m.mark}</span>{" "}
                    <Badge variant="outline" className="text-[10px]">
                      {m.live ? "LIVE" : "DEAD"}
                    </Badge>
                    <br />
                    <span className="text-neutral-500">
                      Owner: {m.owner} · Classes: {m.nice_classes.join(", ")}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main results page
// ---------------------------------------------------------------------------

export default function ResultsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const router = useRouter();

  const [stateResults, setStateResults] = useState<StateResult[]>([]);
  const [usptoResult, setUsptoResult] = useState<UsptoResult | null>(null);
  const [status, setStatus] = useState<"running" | "complete" | "error">("running");
  const [searchName, setSearchName] = useState("");

  useEffect(() => {
    const es = streamResults(jobId);

    es.addEventListener("state_result", (e) => {
      const data: StateResult = JSON.parse(e.data);
      setStateResults((prev) => [...prev, data]);
    });

    es.addEventListener("uspto_result", (e) => {
      const data: UsptoResult = JSON.parse(e.data);
      setUsptoResult(data);
    });

    es.addEventListener("done", (e) => {
      const data = JSON.parse(e.data);
      setStatus(data.status === "error" ? "error" : "complete");
      es.close();
    });

    es.onerror = () => {
      setStatus("error");
      es.close();
    };

    // Fetch job metadata for the name
    fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/jobs/${jobId}`)
      .then((r) => r.json())
      .then((d) => setSearchName(`"${d.name}" (${d.entity_type})`))
      .catch(() => {});

    return () => es.close();
  }, [jobId]);

  const taken = stateResults.filter((r) => r.availability === "taken").length;
  const available = stateResults.filter((r) => r.availability === "available").length;
  const similar = stateResults.filter((r) => r.availability === "similar").length;

  return (
    <main className="mx-auto max-w-2xl px-4 py-10">
      {/* Header */}
      <div className="mb-8">
        <button
          onClick={() => router.push("/")}
          className="mb-4 flex items-center gap-1 text-sm text-neutral-400 hover:text-neutral-600"
        >
          <ArrowLeft className="h-4 w-4" /> New search
        </button>

        <h1 className="text-2xl font-semibold text-neutral-900">
          {searchName || "Searching..."}
        </h1>

        {/* Summary bar */}
        <div className="mt-3 flex flex-wrap items-center gap-4 text-sm text-neutral-500">
          <span className="flex items-center gap-1">
            <CheckCircle className="h-4 w-4 text-green-500" />
            {available} available
          </span>
          <span className="flex items-center gap-1">
            <XCircle className="h-4 w-4 text-red-500" />
            {taken} taken
          </span>
          <span className="flex items-center gap-1">
            <AlertTriangle className="h-4 w-4 text-yellow-500" />
            {similar} similar
          </span>
          {status === "running" && (
            <span className="flex items-center gap-1 text-neutral-400">
              <Loader2 className="h-4 w-4 animate-spin" />
              Checking states...
            </span>
          )}
          {status === "complete" && (
            <span className="text-green-600 font-medium">Complete</span>
          )}
          {status === "error" && (
            <span className="text-red-500 font-medium">Search encountered an error</span>
          )}
        </div>
      </div>

      {/* USPTO result — shown first if available */}
      {usptoResult && (
        <div className="mb-4">
          <UsptoCard result={usptoResult} />
        </div>
      )}

      {/* State results */}
      <div className="space-y-3">
        {stateResults.length === 0 && status === "running" && (
          <div className="flex items-center justify-center py-16 text-neutral-400 gap-2">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-sm">Querying state databases...</span>
          </div>
        )}
        {stateResults.map((r) => (
          <StateCard key={r.state_code} result={r} />
        ))}
      </div>

      {status === "complete" && stateResults.length === 0 && (
        <p className="text-center text-sm text-neutral-400 py-8">
          No state results returned. The search may have encountered an error.
        </p>
      )}

      <p className="mt-10 text-center text-xs text-neutral-400">
        Results are availability indicators only — not legal advice. Consult an attorney before filing.
      </p>
    </main>
  );
}
