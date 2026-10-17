"""Separate laboratory settings from operational prerequisites and limitations."""
import re

TRIAL = re.compile(r'(?:시험|실험|테스트)\s*(?:조건|부하|환경|기관|장소|일시|모델|방법|결과|내용)\s*[:：]|(?:자사\s*)?(?:시험|실험|측정)\s*결과|비교\s*모델\s*[:：]|온열\s*쾌적감|시간\s*단축|균일\s*건조')
OPERATING = re.compile(r'(?:에서만|이하에서|이상에서).{0,16}(?:사용|동작|작동|지원)|(?:사용|작동|동작).{0,16}(?:필요|한정|제한)|최초\s*사용|정보가\s*누적|기능\s*활성화|(?:최대|최소)\s*\d+.{0,18}(?:감지|인식)|(?:감지|인식).{0,16}(?:않을|못할|어려울)')

def condition_type(text, scope=''):
    if OPERATING.search(text):
        return 'operating'
    # A stated limitation on laboratory results is still a limitation under the
    # existing two-axis rubric, but must not be described as a usage prerequisite.
    if re.search(r'(?:시험|실험|측정)\s*결과.{0,80}(?:환경|조건).{0,30}(?:다를|차이|달라)',text) and not re.search(r'(?:시험|실험)\s*(?:부하|조건)\s*[:：]',text):
        return 'test_result_limit'
    if TRIAL.search(text) or TRIAL.search(scope):
        return 'test'
    return 'unspecified'
