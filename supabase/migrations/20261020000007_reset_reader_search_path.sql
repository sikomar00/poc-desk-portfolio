-- 0006 의 역할 기본 검색 경로는 풀러(Supavisor)를 거치면 적용되지 않는 것을 확인했다(읽기 전용·5초 제한은 적용되는데 검색 경로만 안 됨).
-- 효과 없는 설정이 남아 오해를 부르지 않도록 되돌린다. 읽기 전용 계산 역할은 접속 직후 SET search_path 를 보낸다(세션 모드, app/db.py).
alter role poc_reader_demo reset search_path;
