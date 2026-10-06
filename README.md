# Global Inquiry Manager

Mary & May inbound sales inquiry manager built with Streamlit.

The app keeps its local SQLite mode for development and supports a shared Supabase PostgreSQL database for hosted team use. Team members sign in with accounts managed by an administrator in the app.

## Deploy for shared access

1. Review [Supabase and external access setup](SUPABASE_SETUP_KO.md).
2. Deploy `app.py` from this repository to Streamlit Community Cloud.
3. Add `SUPABASE_DB_URL`, `INQUIRY_ADMIN_USERNAME`, and `INQUIRY_ADMIN_PASSWORD` to the app's private Secrets settings. Never commit real credentials or `.streamlit/secrets.toml`.
4. Start the app once to initialize the Supabase tables. Use the migration utility in the setup guide if you need to copy existing SQLite data.

## Files

- `app.py` — application and database setup
- `requirements.txt` — app dependencies
- `migrate_sqlite_to_supabase.py` — guarded one-time SQLite data migration
- `SUPABASE_SETUP_KO.md` — deployment, secrets, and data migration guide
- `.streamlit/secrets.toml.example` — placeholder-only secrets format
