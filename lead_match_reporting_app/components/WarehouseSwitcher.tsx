"use client";

import { useEffect, useState } from "react";
import { usePathname, useSearchParams, useRouter } from "next/navigation";

type WarehouseEntry = { warehouse: string; latestRunId: string; updated: string };

export function WarehouseSwitcher() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const [warehouses, setWarehouses] = useState<WarehouseEntry[]>([]);
  const current = searchParams.get("warehouse") || "";

  useEffect(() => {
    fetch("/api/warehouses")
      .then((res) => (res.ok ? res.json() : []))
      .then((data: WarehouseEntry[]) => setWarehouses(data))
      .catch(() => {});
  }, []);

  function handleChange(value: string) {
    if (!value) {
      router.push("/");
      return;
    }
    const params = new URLSearchParams(searchParams.toString());
    params.set("warehouse", value);
    router.push(`${pathname}?${params.toString()}`);
  }

  if (warehouses.length === 0) return null;

  return (
    <select
      className="warehouse-switcher"
      value={current}
      onChange={(e) => handleChange(e.target.value)}
      aria-label="Select warehouse"
    >
      <option value="">All Warehouses</option>
      {warehouses.map((w) => (
        <option key={w.warehouse} value={w.warehouse}>
          Warehouse {w.warehouse}
        </option>
      ))}
    </select>
  );
}
