import type { TaskStatus } from "./types";

export const STATUS_LABELS: Record<TaskStatus, string> = {
  CREATED: "已创建",
  RUNNING: "执行中",
  WAITING_RISK_APPROVAL: "待风险审批",
  WAITING_HUMAN_INTERVENTION: "需人工介入",
  CANCELLING: "取消中",
  CANCELLED: "已取消",
  COMPLETED: "已完成",
  COMPLETED_NO_CHANGES: "无需修改",
  FAILED: "失败",
  POLICY_REJECTED: "策略拒绝",
};

export function statusTone(status: TaskStatus): string {
  if (["COMPLETED", "COMPLETED_NO_CHANGES"].includes(status)) return "positive";
  if (["FAILED", "POLICY_REJECTED", "CANCELLED"].includes(status)) return "negative";
  if (["WAITING_RISK_APPROVAL", "WAITING_HUMAN_INTERVENTION"].includes(status)) return "warning";
  return "active";
}

export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(date);
}

export function compactId(value?: string | null): string {
  if (!value) return "—";
  return value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-5)}` : value;
}

export function percent(used: number, maximum: number): number {
  if (maximum <= 0) return 0;
  return Math.min(100, Math.round((used / maximum) * 100));
}
