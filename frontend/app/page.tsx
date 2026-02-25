"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { createSearch } from "@/lib/api";

const ENTITY_TYPES = ["LLC", "Corporation", "LP", "LLP", "PC", "PLLC"];

export default function Home() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [entityType, setEntityType] = useState("LLC");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError("");
    try {
      const { job_id } = await createSearch(name.trim(), entityType);
      router.push(`/results/${job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-4">
      <div className="w-full max-w-lg">
        <div className="mb-10 text-center">
          <h1 className="text-3xl font-semibold tracking-tight text-neutral-900">
            Clear Path Entity
          </h1>
          <p className="mt-2 text-neutral-500">
            Check business name availability across U.S. states and USPTO trademarks.
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-xl border border-neutral-200 bg-white p-6 shadow-sm space-y-5"
        >
          <div className="space-y-2">
            <Label htmlFor="name">Business Name</Label>
            <Input
              id="name"
              placeholder="e.g. Apex Solutions"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={loading}
              autoFocus
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="entity-type">Entity Type</Label>
            <Select value={entityType} onValueChange={setEntityType} disabled={loading}>
              <SelectTrigger id="entity-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ENTITY_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <Button type="submit" className="w-full" disabled={loading || !name.trim()}>
            {loading ? "Starting search..." : "Search"}
          </Button>
        </form>

        <p className="mt-6 text-center text-xs text-neutral-400">
          Results are availability indicators only — not legal advice.
        </p>
      </div>
    </main>
  );
}
