import type { ButtonHTMLAttributes, ReactNode } from "react";
import styles from "./ui.module.css";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: "primary" | "secondary" | "danger" | "quiet";
  icon?: ReactNode;
};

export function Button({
  tone = "secondary",
  icon,
  className = "",
  children,
  type = "button",
  ...props
}: Props) {
  return (
    <button
      type={type}
      className={`${styles.button} ${styles[tone]} ${className}`}
      {...props}
    >
      {icon && <span className={styles.buttonIcon}>{icon}</span>}
      <span>{children}</span>
    </button>
  );
}
