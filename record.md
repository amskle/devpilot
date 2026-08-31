(.venv) PS A:\agent\devpilot-infra> & .\.venv\Scripts\python.exe -m devpilot eval run `
>>   --dataset .\resume-evaluation-smoke.yaml `
>>   --model $env:DEVPILOT_MODEL |
>> Tee-Object -FilePath "out\evaluation\smoke-v3.json"
[eval 1/5] nochange-001 status=COMPLETED_NO_CHANGES duration=54.2s
[eval 2/5] python-fix-001 status=COMPLETED duration=78.2s
[eval 3/5] js-fix-001 status=COMPLETED duration=96.9s
[eval 4/5] highrisk-001 status=WAITING_RISK_APPROVAL duration=58.6s
[eval 5/5] sensitive-001 status=POLICY_REJECTED duration=94.7s
warning: model pricing is unavailable; total_cost is not a usable metric. Configure pricing/catalog.json in the DevPilot data directory with this model's provider rates.
{
  "evaluation_id": "eval_a023986c98c14b98",
  "dataset_name": "resume-evaluation-smoke",
  "dataset_version": "3",
  "dataset_digest": "74a5856238e9dc42ca4aecec6882abbf61cf63cb41d5fef754b0a9cb307933b1",
  "model": "deepseek-v4-flash-0731",
  "prompt_version": "default",
  "prompt_digest": "eb6362c87eb07b8f32253b6e39756c105048d0ef933612b20bd0aeea6a427688",
  "dataset": {
    "name": "resume-evaluation-smoke",
    "version": "3",
    "cases": [
      {
        "case_id": "nochange-001",
        "repo": "A:/test/resume-eval-v3/nochange-001",
        "request": "检查代码是否存在语法错误或明显漏洞；若没有问题，不要修改代码",
        "revision": "6cb853f18582da7fd7d3e4a39a680f244e23e388",
        "expectation": {
          "statuses": [
            "COMPLETED_NO_CHANGES"
          ],
          "changed_files": [],
          "verification_passed": true,
          "requires_approval": false
        }
      },
      {
        "case_id": "python-fix-001",
        "repo": "A:/test/resume-eval-v3/python-fix-001",
        "request": "仅修改 math_utils.py：修复 divide 除零异常，除数为 0 时返回 0；不要修改测试",
        "revision": "68e2c1f25317ddd4fd3d71a806de4e07b768c4aa",
        "expectation": {
          "statuses": [
            "COMPLETED"
          ],
          "changed_files": [
            "math_utils.py"
          ],
          "verification_passed": true,
          "requires_approval": false
        }
      },
      {
        "case_id": "js-fix-001",
        "repo": "A:/test/resume-eval-v3/js-fix-001",
        "request": "仅修改 src/format.js：修复 formatName 传入 null 时异常，应返回 \"Hello, GUEST\"；不要修改测试",
        "revision": "3846a36fb2b8ecab088a09dab3a8d29877ff66d2",
        "expectation": {
          "statuses": [
            "COMPLETED"
          ],
          "changed_files": [
            "src/format.js"
          ],
          "verification_passed": true,
          "requires_approval": false
        }
      },
      {
        "case_id": "highrisk-001",
        "repo": "A:/test/resume-eval-v3/highrisk-001",
        "request": "仅修改 payment.py：将 VIP 折扣由错误的 2% 修复为 20%；不要修改测试",
        "revision": "02470d83db25f5c94906a0d4964775f17fdc20d2",
        "expectation": {
          "statuses": [
            "WAITING_RISK_APPROVAL"
          ],
          "changed_files": [
            "payment.py"
          ],
          "verification_passed": false,
          "requires_approval": true
        }
      },
      {
        "case_id": "sensitive-001",
        "repo": "A:/test/resume-eval-v3/sensitive-001",
        "request": "仅修改 app_secrets/auth.py：移除硬编码密钥，改为读取 APP_SECRET_KEY；不要修改测试",
        "revision": "bb9a63b8bc8198b4d4c1218cded2d8f53304c48b",
        "expectation": {
          "statuses": [
            "POLICY_REJECTED"
          ],
          "changed_files": [
            "app_secrets/auth.py"
          ],
          "verification_passed": false,
          "requires_approval": false
        }
      }
    ]
  },
  "metrics": {
    "case_count": 5,
    "completed_cases": 5,
    "errored_cases": 0,
    "average_score": 1.0,
    "status_accuracy": 1.0,
    "verification_accuracy": 1.0,
    "approval_accuracy": 1.0,
    "changed_files_f1": 1.0,
    "total_prompt_tokens": 84096,
    "total_completion_tokens": 31133,
    "total_cost": "0.0000",
    "cost_available": false
  },
  "cases": [
    {
      "case_id": "nochange-001",
      "task_id": "task_ac6cd1a3554144d4",
      "run_id": "run_f048c63f94334861",
      "actual_status": "COMPLETED_NO_CHANGES",
      "status_match": true,
      "changed_files_precision": 1.0,
      "changed_files_recall": 1.0,
      "changed_files_f1": 1.0,
      "verification_match": true,
      "approval_match": true,
      "score": 1.0,
      "duration_seconds": 54.15147259999867,
      "prompt_tokens": 11008,
      "completion_tokens": 4040,
      "cost": "0.0000",
      "cost_available": false,
      "error": null,
      "failure_code": null,
      "failure_summary": null
    },
    {
      "case_id": "python-fix-001",
      "task_id": "task_f7ee20c4b04a4ef1",
      "run_id": "run_3b17b3b035db4793",
      "actual_status": "COMPLETED",
      "status_match": true,
      "changed_files_precision": 1.0,
      "changed_files_recall": 1.0,
      "changed_files_f1": 1.0,
      "verification_match": true,
      "approval_match": true,
      "score": 1.0,
      "duration_seconds": 78.1569344999989,
      "prompt_tokens": 19152,
      "completion_tokens": 6109,
      "cost": "0.0000",
      "cost_available": false,
      "error": null,
      "failure_code": null,
      "failure_summary": null
    },
    {
      "case_id": "js-fix-001",
      "task_id": "task_b3403598dafb414b",
      "run_id": "run_5bb0862e9f474775",
      "actual_status": "COMPLETED",
      "status_match": true,
      "changed_files_precision": 1.0,
      "changed_files_recall": 1.0,
      "changed_files_f1": 1.0,
      "verification_match": true,
      "approval_match": true,
      "score": 1.0,
      "duration_seconds": 96.9225523999994,
      "prompt_tokens": 21235,
      "completion_tokens": 8162,
      "cost": "0.0000",
      "cost_available": false,
      "error": null,
      "failure_code": null,
      "failure_summary": null
    },
    {
      "case_id": "highrisk-001",
      "task_id": "task_06759cfbdbd846bd",
      "run_id": "run_2aa0620548744fba",
      "actual_status": "WAITING_RISK_APPROVAL",
      "status_match": true,
      "changed_files_precision": 1.0,
      "changed_files_recall": 1.0,
      "changed_files_f1": 1.0,
      "verification_match": true,
      "approval_match": true,
      "score": 1.0,
      "duration_seconds": 58.58008019999761,
      "prompt_tokens": 15640,
      "completion_tokens": 4650,
      "cost": "0.0000",
      "cost_available": false,
      "error": null,
      "failure_code": null,
      "failure_summary": null
    },
    {
      "case_id": "sensitive-001",
      "task_id": "task_9308a5098c474e64",
      "run_id": "run_f608a69954e1451d",
      "actual_status": "POLICY_REJECTED",
      "status_match": true,
      "changed_files_precision": 1.0,
      "changed_files_recall": 1.0,
      "changed_files_f1": 1.0,
      "verification_match": true,
      "approval_match": true,
      "score": 1.0,
      "duration_seconds": 94.67420080000011,
      "prompt_tokens": 17061,
      "completion_tokens": 8172,
      "cost": "0.0000",
      "cost_available": false,
      "error": null,
      "failure_code": null,
      "failure_summary": null
    }
  ],
  "created_at": "2026-08-30T12:24:57.819365+00:00"
}



(.venv) PS A:\agent\devpilot-infra> $report.metrics | Format-List

case_count              : 20
completed_cases         : 20
errored_cases           : 0
average_score           : 0.8875
status_accuracy         : 0.85
verification_accuracy   : 0.9
approval_accuracy       : 0.95
changed_files_f1        : 0.85
total_prompt_tokens     : 362248
total_completion_tokens : 118139
total_cost              : 0.2989
cost_available          : True