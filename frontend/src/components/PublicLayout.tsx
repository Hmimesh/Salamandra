import { ArrowRight } from "lucide-react";
import { Link, Outlet, useLocation } from "react-router-dom";

export function PublicLayout() {
  const location = useLocation();
  const onLandingPage = location.pathname === "/";

  return (
    <div className="public-site">
      <header className="public-header">
        <div className={`public-container public-header-inner ${onLandingPage ? "" : "public-header-compact"}`}>
          <Link className="public-brand" to="/" aria-label="Salamandra home">
            <span className="brand-symbol" aria-hidden="true">S</span>
            <strong>SALAMANDRA</strong>
          </Link>
          {onLandingPage ? (
            <nav className="public-nav" aria-label="Public navigation">
              <a href="#product">Product</a>
              <a href="#workflow">How it works</a>
            </nav>
          ) : null}
          <div className="public-header-actions">
            {location.pathname !== "/login" ? <Link className="public-text-link" to="/login">Sign in</Link> : null}
            {location.pathname !== "/register" ? (
              <Link className="button button-primary" to="/register">
                Create workspace<ArrowRight size={16} />
              </Link>
            ) : null}
          </div>
        </div>
      </header>
      <Outlet />
      <footer className="public-footer">
        <div className="public-container public-footer-inner">
          <strong>SALAMANDRA</strong>
          <nav aria-label="Footer navigation">
            <Link to="/legal#terms">Terms</Link>
            <Link to="/legal#privacy">Privacy</Link>
            <Link to="/contact">Contact</Link>
          </nav>
          <span>Event operations, from brief to return.</span>
        </div>
      </footer>
    </div>
  );
}
