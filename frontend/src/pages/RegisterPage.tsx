import { ArrowRight, CheckCircle2 } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Spinner } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";

interface RegistrationFields {
  name: string;
  email: string;
  password: string;
  organizationName: string;
  acceptTerms: boolean;
}

const initialFields: RegistrationFields = {
  name: "",
  email: "",
  password: "",
  organizationName: "",
  acceptTerms: false,
};

export function RegisterPage() {
  const { state, busy, registerWorkspace } = useWorkspace();
  const [fields, setFields] = useState(initialFields);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const errorSummary = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.title = "Create a Salamandra workspace";
  }, []);

  function update<K extends keyof RegistrationFields>(name: K, value: RegistrationFields[K]) {
    setFields((current) => ({ ...current, [name]: value }));
    setErrors((current) => ({ ...current, [name]: "" }));
    setServerError("");
  }

  function validate() {
    const next: Record<string, string> = {};
    if (!fields.name.trim()) next.name = "Enter your name.";
    if (!/^\S+@\S+\.\S+$/.test(fields.email.trim())) next.email = "Enter a valid work email address.";
    if (fields.password.length < 8) next.password = "Use at least 8 characters.";
    if (!fields.organizationName.trim()) next.organizationName = "Enter a workspace name.";
    if (!fields.acceptTerms) next.acceptTerms = "Accept the Terms and Privacy Policy.";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setServerError("");
    if (!validate()) {
      window.setTimeout(() => errorSummary.current?.focus(), 0);
      return;
    }
    try {
      await registerWorkspace({
        name: fields.name,
        email: fields.email,
        password: fields.password,
        organization_name: fields.organizationName,
        accept_terms: fields.acceptTerms,
      });
    } catch (error) {
      setServerError(error instanceof Error ? error.message : "Workspace creation failed. Please try again.");
      window.setTimeout(() => errorSummary.current?.focus(), 0);
    }
  }

  if (state?.auth.registration_mode !== "open") {
    return (
      <main className="auth-page">
        <section className="auth-closed" aria-labelledby="registration-title">
          <CheckCircle2 size={30} aria-hidden="true" />
          <h1 id="registration-title">Workspace registration is currently private.</h1>
          <p>Salamandra is in controlled staging. Use an account provided by your workspace owner.</p>
          <Link className="button button-primary public-cta" to="/login">Sign in<ArrowRight size={17} /></Link>
        </section>
      </main>
    );
  }

  const hasErrors = Boolean(serverError || Object.values(errors).some(Boolean));
  return (
    <main className="auth-page">
      <section className="auth-form-section" aria-labelledby="registration-title">
        <div className="auth-heading">
          <h1 id="registration-title">Create your workspace</h1>
          <p>Set up the home for your events, inventory, and team.</p>
        </div>
        {hasErrors ? <div className="form-error-summary" ref={errorSummary} tabIndex={-1} role="alert"><strong>Check the form</strong><span>{serverError || "Correct the highlighted fields and try again."}</span></div> : null}
        <form className="auth-form" onSubmit={submit} noValidate>
          <label htmlFor="register-name">Full name</label>
          <input id="register-name" name="name" value={fields.name} onChange={(event) => update("name", event.target.value)} autoComplete="name" maxLength={200} aria-invalid={Boolean(errors.name)} aria-describedby={errors.name ? "register-name-error" : undefined} required />
          {errors.name ? <span className="field-error" id="register-name-error">{errors.name}</span> : null}

          <label htmlFor="register-email">Work email</label>
          <input id="register-email" name="email" type="email" inputMode="email" value={fields.email} onChange={(event) => update("email", event.target.value)} autoComplete="email" maxLength={320} aria-invalid={Boolean(errors.email)} aria-describedby={errors.email ? "register-email-error" : undefined} required />
          {errors.email ? <span className="field-error" id="register-email-error">{errors.email}</span> : null}

          <label htmlFor="register-password">Password</label>
          <input id="register-password" name="password" type="password" value={fields.password} onChange={(event) => update("password", event.target.value)} autoComplete="new-password" minLength={8} maxLength={256} aria-invalid={Boolean(errors.password)} aria-describedby={errors.password ? "register-password-help register-password-error" : "register-password-help"} required />
          <span className="field-help" id="register-password-help">Use at least 8 characters. Passwords can be pasted and saved in your password manager.</span>
          {errors.password ? <span className="field-error" id="register-password-error">{errors.password}</span> : null}

          <label htmlFor="register-workspace">Workspace name</label>
          <input id="register-workspace" name="organization" value={fields.organizationName} onChange={(event) => update("organizationName", event.target.value)} autoComplete="organization" maxLength={200} aria-invalid={Boolean(errors.organizationName)} aria-describedby={errors.organizationName ? "register-workspace-error" : undefined} required />
          {errors.organizationName ? <span className="field-error" id="register-workspace-error">{errors.organizationName}</span> : null}

          <label className="auth-checkbox" htmlFor="accept-terms">
            <input id="accept-terms" type="checkbox" checked={fields.acceptTerms} onChange={(event) => update("acceptTerms", event.target.checked)} aria-invalid={Boolean(errors.acceptTerms)} aria-describedby={errors.acceptTerms ? "accept-terms-error" : undefined} />
            <span>I agree to the <Link to="/terms">Terms</Link> and <Link to="/privacy">Privacy Policy</Link>.</span>
          </label>
          {errors.acceptTerms ? <span className="field-error" id="accept-terms-error">{errors.acceptTerms}</span> : null}

          <button className="button button-primary public-cta auth-submit" type="submit" disabled={busy}>
            {busy ? <Spinner label="Creating workspace" /> : null}<span>{busy ? "Creating workspace" : "Create workspace"}</span><ArrowRight size={18} />
          </button>
        </form>
        <p className="auth-switch">Already have a workspace? <Link to="/login">Sign in</Link></p>
      </section>
    </main>
  );
}
