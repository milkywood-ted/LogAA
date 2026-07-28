# fixtures — 재현 가능한 테스트 입력

## `known_answer_ioctl_deadlock.log`

**정답을 아는 시나리오.** 자료에 `CONFIRMED 4.1-1` 로 기록된 결함(ioctl 락 누수 → 데드락)의
로그를 심어, 파이프라인이 그 맥락을 제대로 모아 오는지 확인한다.

- 대상: FRC / rheam
- 구성: 채널 전환에 따른 정상 모드 변경 시퀀스 → `[sdp_frc_do_ioctl]: error with copy_from_user`
  → 이후 FRC 로그 단절(USB·EXT4 만 지속)
- 문제 상황(같이 줄 것): "채널 전환 직후 화면이 정지하고 리모컨 입력에 반응하지 않는다는 신고.
  재부팅하면 복구됨."

**합성 로그다.** 실제 기기에서 캡처한 것이 아니라 `11_log_index.tsv` 의 실제 `match_key` 로
구성했다 — 시그니처는 진짜지만 시나리오는 만든 것이다.

### 왜 이 결함을 골랐나

세 가지가 한 번에 검사된다:

1. **§ 발췌 정확도** — `03 §5.1 CONFIRMED` 와 `12 §2 ioctl 수명` 이 선택돼야 한다.
   후자에는 "이 라인 후 ioctl 로그가 끊기면 데드락 강한 신호" 라는 판정 힌트가 있다.
2. **동명 시그니처 함정 회피** — `error with copy_from_user` 는 ioctl 경로와 debugfs 경로
   양쪽에 존재하고 대괄호 접두로만 구분된다. `sdp_frc_ioctl.c:277` 로 해소돼야 맞다.
3. **부재 추론** — 로그 단절이 근거가 되는데, 기본 레벨에서 ready 상태는 원래 무로그라
   (`04 §3.3`) 성급히 단정하면 안 된다. 반증 정황을 스스로 찾는지 본다.

### 실행

```sh
cd TentativeDefectTriage
python3 analyze/analyze.py fixtures/known_answer_ioctl_deadlock.log \
    --module-root <자료>/analysis/sdp_frc --chip rheam \
    --profile DTV_FRC --keywords "S_F" \
    --problem "채널 전환 직후 화면이 정지하고 리모컨 입력에 반응하지 않는다는 신고. 재부팅하면 복구됨." \
    --mode single --dry-run --dump-prompt /tmp/prompt.txt
```

프롬프트는 결정론적으로 재생성되므로 저장소에 담지 않는다(약 67k자). `--dry-run` 을 빼면
설정된 LLM 으로 실제 호출한다. 다른 모델로 시험하려면 `--dump-prompt` 로 뽑아 그 모델에
그대로 넣으면 된다 — 같은 입력·같은 자료면 같은 프롬프트가 나온다.

**자료 버전에 따라 프롬프트가 달라진다.** 분석자료가 갱신되면 발췌 결과가 바뀐다
(`material/verify_material.py` 로 변화를 감지할 수 있다).
