import type { ReactNode } from "react";
import styles from "./ui.module.css";

export function StatusBadge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "success" | "attention" | "danger" | "info";
  children: ReactNode;
}) {
  return <span className={`${styles.status} ${styles[`status_${tone}`]}`}>{children}</span>;
}
