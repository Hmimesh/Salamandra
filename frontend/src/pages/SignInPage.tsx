import { ArrowRight } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Spinner } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";

export function SignInPage() {
  const { state, busy, signIn, signInDemo } = useWorkspace();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const errorBox = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.title = "Sign in to Salamandra";
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      await signIn(email, password);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Sign-in failed. Try again.");
      window.setTimeout(() => errorBox.current?.focus(), 0);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-form-section" aria-labelledby="signin-title">
        <div className="auth-heading">
          <h1 id="signin-title">Sign in</h1>
          <p>Open your company workspace.</p>
        </div>
        {error ? <div className="form-error-summary" ref={errorBox} tabIndex={-1} role="alert"><strong>Could not sign in</strong><span>{error}</span></div> : null}
        <form className="auth-form" onSubmit={submit}>
          <label htmlFor="signin-email">Work email</label>
          <input id="signin-email" name="email" type="email" inputMode="email" value={email} onChange={(event) => { setEmail(event.target.value); setError(""); }} autoComplete="username" maxLength={320} required />
          <label htmlFor="signin-password">Password</label>
          <input id="signin-password" name="password" type="password" value={password} onChange={(event) => { setPassword(event.target.value); setError(""); }} autoComplete="current-password" maxLength={256} required />
          <button className="button button-primary public-cta auth-submit" type="submit" disabled={busy}>
            {busy ? <Spinner label="Signing in" /> : null}<span>{busy ? "Signing in" : "Sign in"}</span><ArrowRight size={18} />
          </button>
          {state?.auth.demo_available ? (
            <button className="button button-secondary public-cta" type="button" onClick={() => void signInDemo().catch(() => undefined)} disabled={busy}>Open local demo workspace</button>
          ) : null}
        </form>
        <p className="auth-switch">Need a workspace? <Link to="/register">Create one</Link></p>
        <div className="auth-help-links"><Link to="/legal#privacy">Privacy</Link><Link to="/contact">Contact support</Link></div>
      </section>
    </main>
  );
}
