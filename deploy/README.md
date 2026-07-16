# LogAA 서비스화 가이드 (systemd user 모드)

수동 `run_*.sh` 기동을 systemd 서비스로 전환한다 (시스템 설계서 §9-3).
**sudo 없이** 설치·운영 가능하도록 user 모드를 기준으로 한다.

효과: 프로세스가 죽으면 자동 재시작(`Restart=on-failure`), 기동 순서 보장
(AA·puller → backend), 로그 journald 수집, 표준 명령으로 기동/중지/상태 확인.

## 전제

- 리눅스 서버 + systemd (대부분의 배포판 기본)
- 저장소가 `~/LogAA` 에 있고 공유 가상환경이 `~/LogAA/.venv` 에 준비됨
  (다른 경로면 유닛 파일의 `%h/LogAA` 부분을 절대 경로로 수정)
- frontend 는 서비스가 아니다 — `build_frontend.sh` 로 빌드하면 backend 가 서빙
- puller 인증서(`puller/certs/`)·config 의 OS 의존 설정이 서버에 맞게 배치됨

## 설치 (sudo 불필요)

```bash
mkdir -p ~/.config/systemd/user
cp ~/LogAA/deploy/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload

# backend 를 켜면 Wants= 로 AA·puller 가 함께 기동된다
systemctl --user enable --now logaa-backend
```

## 일상 운영

```bash
systemctl --user status logaa-backend logaa-aa logaa-puller   # 상태
journalctl --user -u logaa-backend -f                          # 로그 실시간
systemctl --user restart logaa-aa                              # 개별 재시작
systemctl --user stop logaa-backend                            # 중지
```

코드 업데이트 반영:

```bash
cd ~/LogAA && git pull
cd frontend && ./build_frontend.sh          # 프론트 변경이 있을 때
systemctl --user restart logaa-aa logaa-puller logaa-backend
```

## 부팅 시 자동 기동 (linger)

user 서비스는 기본적으로 **로그인 시** 시작된다. 로그인 없이 부팅만으로
기동하려면 linger 를 켠다:

```bash
loginctl enable-linger $USER      # 관리자 승인이 필요할 수 있음:
                                  #   sudo loginctl enable-linger <계정명>  (1회)
loginctl show-user $USER | grep Linger   # Linger=yes 확인
```

linger 를 켤 수 없는 환경이라면: **재부팅 후 해당 계정으로 SSH 로그인 1회**가
기동 트리거가 된다 (이후 죽어도 자동 재시작은 계속 동작).

## sudo 가 있는 경우 — system 모드 변형 (선택)

`/etc/systemd/system/` 에 복사해 시스템 서비스로 운영할 수도 있다. 유닛에서
다음을 수정한다:

- `[Service]` 에 `User=<계정명>` 추가
- `%h` 지정자는 system 모드에서 root 홈으로 해석되므로 **절대 경로로 교체**
- `[Install]` 의 `WantedBy=default.target` → `WantedBy=multi-user.target`
- `[Unit]` 에 `After=network-online.target` / `Wants=network-online.target` 추가 가능

이후 `sudo systemctl enable --now logaa-backend`. linger 불필요(부팅 자동 기동).

## 검증 체크리스트 (서버 적용 후)

1. `systemctl --user status` 3종 모두 `active (running)`
2. `http://<서버IP>:8800` 접속 → 화면·분석 정상
3. `kill <backend pid>` → 5초 내 자동 재시작 확인
4. (linger 설정 시) 재부팅 → 로그인 없이 3종 자동 기동 확인
