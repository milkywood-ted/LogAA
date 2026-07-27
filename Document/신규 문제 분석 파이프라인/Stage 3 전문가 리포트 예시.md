# Stage 3 전문가 리포트 예시

작성일: 2026-07-27
관련: `신규 문제 분석 파이프라인 설계.md` §5 "Stage 3 산출물 스키마 — `ExpertReport`"

이 문서는 `ExpertReport` 스키마가 실제로 어떤 내용을 담게 되는지 보여주는 예시 인스턴스다. 시나리오(문제 상황·로그)는 설명을 위해 구성한 것이지만, 인용된 근거(로그 시그니처, `file:line`, 결함 매트릭스 항목)는 전부 `DTV_soc_driver/analysis/sdp_frc/rheal/`의 실제 내용이다 — 자료가 실제로 이런 품질의 근거를 제공한다는 걸 보여주기 위해 가짜 인용을 쓰지 않았다.

## 가정 시나리오

- **문제 상황(problem_text)**: "rheal 칩 세트에서 채널 전환 중 화면이 정지하고 리모컨 입력에 응답이 없다는 신고 다수 접수."
- **지정된 전문가**: `DTV_FRC` (chip=`rheal`)
- **정제된 로그(Stage 1 출력, 발췌)**: 채널 전환 시각 전후 구간에 아래 라인이 포함되어 있고, 그 이후로 FRC 관련 로그가 전혀 없다.

```
[  812.441200] [sdp_frc_do_ioctl] : exceed argument size limit!!
```

## ExpertReport 인스턴스

```yaml
profile_name: DTV_FRC
chip: rheal
module_root: analysis/sdp_frc
build_assumption: >
  -DSOC_RHEAL -DELEM_RHEAL -DLOW_LATENCY_SUPPORT -DMANAGED_RAST,
  CONFIG_AV=no, CONFIG_LFD=n, PANELTEST/UTC/SMEM off
  (출처: analysis/sdp_frc/rheal/10_summary_and_findings.md 도입부)

hypotheses:
  - summary: >
      ioctl 인자 크기 검증 실패 경로에서 락 해제 없이 반환 →
      이후 모든 ioctl이 데드락(CONFIRMED 결함)
    confidence: 높음
    narrative: >
      (사실) 정제된 로그에서 `[sdp_frc_do_ioctl] : exceed argument size limit!!`
      (ALERT)가 매칭됨 — analysis/sdp_frc/rheal/11_log_index.tsv 기준
      sdp_frc/rheal/sdp_frc_ioctl.c:279.
      (사실) 같은 파일 :272에서 `ioctl_lock` mutex를 획득한 뒤, 인자 크기
      초과(:280)와 copy_from_user 실패(:286) 두 경로 모두 `mutex_unlock`
      없이 `return`한다 — analysis/sdp_frc/rheal/03_ioctl_abi.md:166-167,
      CONFIRMED(analysis/sdp_frc/rheal/10_summary_and_findings.md §4.1-1).
      (추론) 이 로그 이후 정제된 로그에 ioctl 관련 라인이 전혀 없다는 것은
      그 뒤 ioctl 응답이 끊겼을 가능성을 시사하며, 이는
      analysis/sdp_frc/rheal/11_log_triage.md가 명시한 "이 라인 후 ioctl
      무응답이면 데드락 강한 신호(신뢰도 높음)" 패턴과 일치한다.
      (가정) 이 결론은 위 build_assumption 전제 위에서만 유효하다. 사용자
      스페이스의 어느 프로세스가 어떤 인자로 이 ioctl을 호출했는지(실수
      또는 악성 호출)는 이 자료만으로 확인할 수 없다.
    evidence:
      - type: log_line
        value: "[sdp_frc_do_ioctl] : exceed argument size limit!!"
      - type: doc_citation
        value: "sdp_frc/rheal/sdp_frc_ioctl.c:272,279-280,285-286 (via analysis/sdp_frc/rheal/03_ioctl_abi.md:166-167)"
      - type: doc_citation
        value: "analysis/sdp_frc/rheal/10_summary_and_findings.md §4.1-1 (CONFIRMED)"
    counter_points:
      - >
        이번 정제 로그에 재부팅/모듈 재적재 흔적이 없어 "완전 정지" 가설과
        일관되지만, 만약 이 로그 이후에도 다른 무관한 ioctl이 정상 응답한
        기록이 있다면 이 가설은 약해진다 — 이번 입력 로그에서는 그런 라인이
        확인되지 않았다.

unresolved:
  - 실제로 어느 프로세스/앱이 어떤 인자로 이 ioctl을 호출했는지는 이 자료만으로 알 수 없음(사용자 스페이스 원인).
  - copy_from_user 실패 쪽(:285) 시그니처는 이번 로그에 없어, 두 경로 중 정확히 어느 쪽인지는 :279 시그니처 기준으로만 추정한 것.

warnings:
  - 기본 로그 레벨(=1)에서는 ALERT/ERROR만 보이므로, 레벨이 더 낮게 설정된 환경에서는 이 시그니처 자체가 안 보일 수 있다 — 로그가 없다고 이 결함이 없다는 뜻은 아니다.
  - 이 분석은 특정 시점 소스 스냅샷(analysis/sdp_frc) 기준이다 — 최신 소스와 대조를 권장한다.
```

## 이 예시가 보여주는 것

- **가설이 하나로 단정되지 않음** — `confidence: 높음`이지만 `unresolved`·`counter_points`가 남아 있어 "확정 진단"이 아니라 "근거가 강한 하나의 참고 가설"로 읽힌다.
- **모든 주장에 근거가 붙음** — 로그 원문 1개, 소스 인용 2개(`file:line` 포함). 사용자가 원하면 직접 grep해서 검증 가능하다.
- **사실/추론/가정이 문장 단위로 구분됨** — DTV_soc_driver 자료의 규율을 그대로 승계했다.
- **다른 전문가(예: `DTV_DP`)가 같은 defect에 동시에 투입되면**, 이 리포트와 나란히 별도의 `ExpertReport`가 나오고 — 승자 선정 없이 둘 다 사용자에게 제시된다(설계 §3).
