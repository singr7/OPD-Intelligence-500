import { StaffLogin } from "@/app/_components/StaffLogin";

export function Login({ onToken }: { onToken: (token: string) => void }) {
  return (
    <StaffLogin
      role="Queue coordinator"
      description="Monitor patient flow, manage priority, and keep departments moving."
      defaultPhone="+915550000002"
      onToken={onToken}
    />
  );
}
