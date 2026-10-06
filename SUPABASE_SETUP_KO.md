# Global Inquiry Manager · Supabase 및 외부 접속 준비

이 앱은 **Streamlit Community Cloud에 앱을 한 개 배포하고, Supabase PostgreSQL에 팀 데이터를 함께 저장**하는 구성입니다. 각 팀원은 같은 HTTPS 주소로 접속해 앱의 계정 화면에서 로그인합니다. 앱의 데이터베이스 접속 문자열은 서버 비밀 설정에만 저장하고 GitHub에는 올리지 않습니다.

## 1. Supabase 프로젝트 만들기

1. [Supabase](https://supabase.com/dashboard)에 로그인해 새 프로젝트를 만듭니다.
2. 프로젝트 이름과 데이터베이스 암호를 설정하고, 회사 정책에 맞는 데이터 지역을 고릅니다.
3. 프로젝트가 준비되면 **Connect**를 열고 **Session pooler** 연결 문자열을 복사합니다. Streamlit 호스트의 IPv4 환경과 호환성이 필요할 때 쓸 수 있는 연결 방식입니다. Supabase는 IPv4 전용 환경에서 Shared Pooler를 안내하며, 연결 문자열의 사용자 이름과 호스트는 화면에서 복사한 값을 그대로 써야 합니다. [Supabase 연결 안내](https://supabase.com/docs/guides/database/connecting-to-postgres)
4. 연결 문자열의 `[YOUR-PASSWORD]` 부분만 프로젝트 생성 때 설정한 DB 암호로 바꿉니다. 암호에 `@`, `#`, `?`, `/`, 공백 같은 문자가 있으면 URI 인코딩이 필요하므로 먼저 단순한 강한 DB 암호를 설정하거나, 각 문자를 URI 인코딩한 값을 사용합니다.

첫 실행 시 앱이 `companies`, `activities`, `users` 테이블과 필요한 추가 열을 생성합니다. 별도 SQL 실행은 필요하지 않습니다.

## 2. 배포 파일을 GitHub에 올리기

GitHub 저장소에 아래 파일을 포함합니다.

- `app.py`
- `requirements.txt`
- `migrate_sqlite_to_supabase.py`

다음 파일은 저장소에 올리지 않습니다.

- `.streamlit/secrets.toml` (예시 파일 `secrets.toml.example`만 공유 가능)
- `inquiry_manager.db`, 다른 SQLite 데이터베이스 파일
- `.env` 또는 암호가 들어 있는 파일

이 폴더의 `.gitignore`는 이 파일들을 제외하도록 준비돼 있습니다. GitHub 저장소는 앱 소스가 공개되지 않도록 Private으로 두는 것을 권장합니다.

## 3. Streamlit Community Cloud에 배포하기

1. [Streamlit Community Cloud](https://share.streamlit.io/)에 접속해 GitHub 계정을 연결합니다.
2. **Create app**에서 저장소, 배포 브랜치, 진입 파일 `app.py`를 선택합니다.
3. Advanced settings에서 Python 3.12를 선택하고, **Secrets**에 아래 값을 입력합니다. 예시 주소를 실제 Session pooler 주소로 바꾸고, 암호는 실제 값으로 입력합니다.

```toml
SUPABASE_DB_URL = "postgresql://postgres.<PROJECT-REF>:<DB-PASSWORD>@<SESSION-POOLER-HOST>:5432/postgres"
INQUIRY_ADMIN_USERNAME = "Admin"
INQUIRY_ADMIN_PASSWORD = "여기에_8자_이상의_고유한_관리자_암호"
```

실제 키나 암호는 채팅, 소스 코드, GitHub에 붙여 넣지 말고 배포 서비스의 비밀 설정에만 넣습니다. Streamlit은 배포 앱의 비밀 값을 App settings에서 설정·변경하도록 안내합니다. [Streamlit 비밀 설정 안내](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

4. **Deploy**를 누르고 로그에서 앱 시작 여부를 확인합니다. 정상 시작 시 로그인 화면이 표시됩니다.
5. 앱의 공개 URL은 외부에서 접근 가능하도록 공유하되, 앱 내부에서는 로그인 계정이 있어야 데이터를 볼 수 있습니다. Streamlit Cloud의 앱 공유 설정을 Public으로 두면 로그인 화면은 누구나 열 수 있지만, 앱의 데이터 화면은 사용자 ID와 암호 확인 뒤에 표시됩니다. 조직이 Cloud 플랫폼 자체의 추가 로그인 제한을 요구한다면 공유 설정을 별도로 검토하세요.
6. 관리자 로그인 후 **Team Accounts**에서 팀원별 계정을 추가하거나 암호를 재설정합니다. 계정 암호는 앱 데이터베이스에 salted hash로 저장됩니다.

Supabase DB 암호가 포함된 `SUPABASE_DB_URL`은 앱 서버에서만 사용합니다. 브라우저에 보내는 코드에 DB 암호나 Supabase의 `service_role` 키를 넣지 않습니다. Streamlit secrets는 앱의 비밀 설정으로 보관합니다. [Streamlit 비밀 관리](https://docs.streamlit.io/deploy/concepts/secrets)

## 4. 현재 SQLite 데이터 옮기기

현재 사용 중인 `inquiry_manager.db`를 그대로 Supabase로 옮기려면:

1. 기존 앱을 종료하고 SQLite 파일을 별도 위치에 백업합니다.
2. 새 Streamlit 앱을 한 번 실행해 Supabase 테이블이 만들어졌는지 확인합니다.
3. 기존 DB 파일을 이 프로젝트 폴더에 복사하지 않고, 실제 경로를 사용해 로컬 PowerShell에서 마이그레이션 스크립트를 실행합니다.
4. 먼저 의존성을 설치하고, 현재 PowerShell 창에서 연결 문자열을 환경 변수로 넣은 뒤 다음을 실행합니다.

```powershell
py -m pip install -r requirements.txt
$env:SUPABASE_DB_URL = "여기에_Session_pooler_연결문자열"
py migrate_sqlite_to_supabase.py "C:\Global_Inquiry_Manager\inquiry_manager.db"
Remove-Item Env:SUPABASE_DB_URL
```

스크립트는 대상 Supabase에 회사나 활동 데이터가 이미 있으면 중단하고, 열 이름이 맞지 않아 데이터를 버리게 되는 경우에도 중단합니다. 회사, 활동 이력, 사용자 계정(암호 hash 포함)의 ID를 보존해 복사하고 자동 증가 ID의 다음 값도 맞춥니다. 성공 확인 전까지 원본 SQLite 백업을 삭제하지 않습니다.

## 5. 현재 진행 상태와 남은 작업

- [x] 최신 화면과 기존 SQLite 동작을 유지하면서 PostgreSQL 연결 코드를 추가
- [x] 첫 시작 시 필요한 테이블·열을 자동으로 준비
- [x] 배포용 의존성, 비밀 설정 예시, SQLite → Supabase 이관 스크립트 준비
- [ ] Supabase 프로젝트 생성 및 실제 연결 문자열 등록
- [ ] GitHub 저장소에 소스 게시
- [ ] Streamlit Community Cloud 배포 및 실제 외부 로그인 확인
- [ ] 기존 DB 파일 이관 및 팀 계정 점검

실제 Supabase 프로젝트와 배포 계정에 로그인할 권한이나 접속 정보가 아직 연결되지 않아, 클라우드 데이터베이스 연결 및 외부 URL 동작은 아직 확인하지 않았습니다.
