import { StaffLogin } from "@/app/_components/StaffLogin";

export function Login({ onToken }: { onToken: (token: string) => void }) {
  return (
    <StaffLogin
      role="Doctor"
      description="Review the patient queue, document consultations, and issue prescriptions."
      defaultPhone="+915550001001"
      onToken={onToken}
    />
  );
}
