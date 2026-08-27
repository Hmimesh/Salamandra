import { ArrowRight, CalendarCheck2, PackageCheck, ShieldCheck } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useWorkspace } from "../context/WorkspaceContext";
import { Spinner } from "../components/ui";

export function SignInPage() {
  const { state, busy, signIn, signInDemo } = useWorkspace();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    await signIn(email, password).catch(() => undefined);
  }

  return (
    <main className="signin-page">
      <section className="signin-product">
        <div className="signin-brand"><span>S</span><strong>SALAMANDRA</strong></div>
        <div className="signin-message">
          <h1>Every event ready.<br />Every item accounted for.</h1>
          <p>The operations workspace for event teams, production crews, and shared warehouses.</p>
          <ul>
            <li><CalendarCheck2 size={21} /><span><strong>Plan from the brief</strong>Turn plain-language requirements into a real stock plan.</span></li>
            <li><PackageCheck size={21} /><span><strong>Control the warehouse</strong>Shared stock, personal gear, check-out, and returns.</span></li>
            <li><ShieldCheck size={21} /><span><strong>Catch conflicts early</strong>Know what is missing or reserved before show day.</span></li>
          </ul>
        </div>
        <small>Built for the people responsible when the doors open.</small>
      </section>

      <section className="signin-form-wrap">
        <form className="signin-form" onSubmit={submit}>
          <div className="signin-form-head"><span className="brand-symbol">S</span><div><strong>SALAMANDRA</strong><small>Event Operations</small></div></div>
          <h2>Sign in</h2>
          <p>Open your company workspace.</p>
          <label>Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="username" required /></label>
          <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></label>
          <button className="button button-primary button-large" type="submit" disabled={busy}>
            {busy ? <Spinner label="Signing in" /> : null}<span>Sign in</span><ArrowRight size={18} />
          </button>
          {state?.auth.demo_available ? <><div className="signin-divider"><span>or</span></div><button className="button button-secondary button-large" type="button" onClick={() => void signInDemo().catch(() => undefined)} disabled={busy}>Open demo workspace</button></> : null}
          <div className="signin-links"><a href="/legal">Privacy</a><a href="/contact">Contact</a></div>
        </form>
      </section>
    </main>
  );
}
