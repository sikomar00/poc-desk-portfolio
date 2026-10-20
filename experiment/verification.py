"""정답 없는 지표를 위한 검증 방법의 공통 규칙.

사전 지정 평가(evaluate_v2.py)와 소급 시험(verify_without_gold.py)이 같은 규칙을 쓰도록 한곳에 둔다.
두 파일에 복사해 두면 한쪽만 고쳤을 때 두 결과를 비교할 수 없게 되기 때문이다.
"""
import run_experiment as R  # 지표별 허용 오차(TOLERANCE)를 채점과 똑같이 쓴다


def agree(metric, a, b):
    """두 값이 지표의 허용 오차 안에서 같은가. 값이 없으면(None) 같다고 보지 않는다."""
    return a is not None and b is not None and abs(a - b) <= R.TOLERANCE[metric]


def cross_model_flag(metric, mine, other):
    """방법 1(교차 모델 불일치): 다른 모델의 값이 없거나 허용 오차 밖으로 다르면 경보."""
    return not agree(metric, mine, other)


def repeat_flag(metric, values, need):
    """방법 3(반복 불일치): 서로 일치하는 값이 need 번 이상 되는 묶음이 없으면 경보.
    3'는 need=4, 3은 need=5(5/5 일치만 통과)."""
    best = max((sum(agree(metric, a, b) for b in values) for a in values if a is not None), default=0)
    return best < need
