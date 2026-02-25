export type Availability = "available" | "taken" | "similar" | "unknown" | "error";
export type RiskLevel = "low" | "medium" | "high" | "unknown";
export type Confidence = number; // 0.0 – 1.0

export interface StateResult {
  state_code: string;
  state_name: string;
  availability: Availability;
  confidence: Confidence;
  similar_names: string[];
  flags: string[];
  raw_matches: Record<string, string>[];
  notes: string;
}

export interface UsptoResult {
  exact_matches: TrademarkMark[];
  similar_marks: TrademarkMark[];
  risk_level: RiskLevel;
  notes: string;
}

export interface TrademarkMark {
  mark: string;
  status: string;
  serial_number: string;
  owner: string;
  nice_classes: string[];
  live: boolean;
}

export interface EntityDetail {
  file_number: string;
  entity_name: string | null;
  entity_kind: string | null;
  formation_date: string | null;
  registered_agent: string | null;
  opencorporates_url: string;
  error?: string;
  cached: boolean;
}
