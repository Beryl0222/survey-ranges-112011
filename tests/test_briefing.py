"""工具行为测试：定位歧义、版本边界、签核覆盖与追踪。"""
from pathlib import Path
import unittest

from briefing.assess import Assessment
from briefing.chainage import format_chainage, parse_chainage
from briefing.corpus import Corpus
from briefing.designlog import DesignLog
from briefing.directives import check_integrity
from briefing.facts import FactBuilder
from briefing.locate import Locator
from briefing import text as T

ROOT = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus(ROOT)
        cls.locator = Locator(cls.corpus)
        cls.builder = FactBuilder(cls.corpus, cls.locator)
        cls.design = DesignLog(cls.corpus, cls.locator)
        cls.assessment = Assessment(cls.corpus, cls.locator, cls.builder, cls.design)
        cls.facts = cls.builder.all_facts()

    def fact(self, letter_id, needle):
        found = [
            row for row in self.facts
            if row.letter_id == letter_id and needle in row.小句
        ]
        self.assertTrue(found, f"{letter_id} 中找不到含“{needle}”的事实")
        return found[0]


class ChainageTests(Base):
    def test_parse_and_format_round_trip(self):
        self.assertEqual(parse_chainage("K8+000"), 8000)
        self.assertEqual(parse_chainage("k5+240"), 5240)
        self.assertAlmostEqual(parse_chainage("K1+002.5"), 1002.5)
        self.assertEqual(format_chainage(8000), "K8+000")

    def test_rejects_bad_chainage(self):
        for text in ("8+000", "K8", "K8+1000", ""):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_chainage(text)

    def test_discontinuity_two_chainages_one_coordinate(self):
        left = self.locator.chainage_candidates("K8+200", "正线", "乙")
        right = self.locator.chainage_candidates("K8+100", "正线", "乙")
        self.assertEqual({row["道路坐标"] for row in left}, {500})
        self.assertEqual({row["道路坐标"] for row in right}, {500})

    def test_discontinuity_becomes_locator_question(self):
        resolution = self.locator.resolve_points("正线K8+200到K8+100", "正线", "乙")
        self.assertTrue(resolution.break_point)
        joined = " ".join(resolution.notes)
        self.assertIn("K8+200", joined)
        self.assertIn("K8+100", joined)


class LandmarkTests(Base):
    def test_same_name_bridge_keeps_all_candidates(self):
        coord, candidates, notes, questions = self.locator.resolve_landmark(
            "东沟桥", "正线", "乙"
        )
        self.assertEqual(coord, 500)
        self.assertEqual(len(candidates), 3)
        self.assertIn("同名地物", " ".join(notes))

    def test_bridge_on_other_line_is_a_question(self):
        coord, candidates, notes, questions = self.locator.resolve_landmark(
            "东沟桥", "借用线", "乙"
        )
        self.assertEqual(coord, 500)
        self.assertTrue(questions)
        self.assertTrue(any("借用线" in q for q in questions))

    def test_unnamed_bridge_face_lists_candidates(self):
        resolution = self.locator.resolve_points("桥界面东侧路肩已试挖", None, "乙")
        self.assertIsNone(resolution.low)
        self.assertTrue(resolution.candidates)
        self.assertTrue(any("没有点名桥梁" in q for q in resolution.questions))

    def test_nearby_has_no_range(self):
        fact = self.fact("B3", "东沟桥附近")
        self.assertEqual(fact.low, fact.high)
        self.assertTrue(fact.附近)
        self.assertTrue(any("附近" in q for q in fact.定位问题))

    def test_same_sentence_bridge_sets_one_end_of_range(self):
        fact = self.fact("B1", "界石")
        self.assertEqual((fact.low, fact.high), (500, 800))
        self.assertTrue(any("回指" in note or "同句" in note for note in fact.备注))

    def test_cross_line_reference_needs_confirmation(self):
        e8 = [row for row in self.design.entries if row.id == "E8"][0]
        self.assertTrue(e8.问题)
        self.assertTrue(any("借用线" in q for q in e8.问题))


class NatureTests(Base):
    def test_unexamined_is_not_exclusion(self):
        fact = self.fact("B1", "界石")
        self.assertEqual(fact.性质, "未覆盖调查")
        self.assertEqual(fact.覆盖, "未覆盖")
        self.assertFalse(fact.排除)

    def test_exclusion_attaches_to_previous_fact(self):
        fact = self.fact("B4", "未见湿陷层")
        self.assertTrue(any("西侧" in item for item in fact.排除))

    def test_negative_finding_is_limited(self):
        fact = self.fact("B4", "未见湿陷层")
        self.assertEqual(fact.性质, "未见（有限范围）")
        self.assertIn("限本句范围", fact.覆盖)

    def test_suspicion_without_test_is_uncovered(self):
        fact = self.fact("B3", "东沟桥附近")
        self.assertEqual(fact.性质, "推测（未测）")
        self.assertEqual(fact.覆盖, "未覆盖")

    def test_scope_only_is_not_examination(self):
        fact = self.fact("B1", "连接线只核两端衔接")
        self.assertEqual(fact.性质, "范围限定")
        self.assertTrue(fact.边界点)

    def test_unstated_lateral_does_not_become_full_coverage(self):
        fact = self.fact("B1", "新线小岬至东沟桥")
        self.assertEqual(fact.横向, [])
        self.assertEqual(sorted(fact.单元), sorted(
            ["主线北半幅", "主线南半幅", "北肩", "南肩"]
        ))
        self.assertTrue(any("未说明" in note for note in fact.备注))

    def test_north_shoulder_only(self):
        fact = self.fact("B2", "北侧路肩")
        self.assertEqual(fact.单元, ["北肩"])

    def test_main_line_missing_pit_names_both_half_widths(self):
        fact = self.fact("B2", "主线仍缺试坑")
        self.assertEqual(sorted(fact.单元), ["主线北半幅", "主线南半幅"])

    def test_methods_are_recorded(self):
        fact = self.fact("B2", "北侧路肩")
        self.assertIn("试坑", fact.方法)


class LocatorTests(Base):
    def test_interpolation_between_points(self):
        coord, notes, questions = self.locator.interpolate_chainage(
            "K5+240", "正线", "甲"
        )
        self.assertEqual(coord, 740)
        self.assertTrue(questions)

    def test_interpolation_refused_inside_discontinuity(self):
        coord, notes, questions = self.locator.interpolate_chainage(
            "K8+150", "正线", "乙"
        )
        self.assertIsNone(coord)
        self.assertTrue(any("断链" in q for q in questions))

    def test_old_chainage_has_multiple_candidates(self):
        candidates, notes, questions = self.locator.resolve_old_chainage(
            "K5+000", "正线", "甲", "乙"
        )
        coords = {candidate.坐标 for candidate in candidates}
        self.assertGreaterEqual(len(candidates), 2)
        self.assertTrue(questions)
        self.assertEqual(coords, {500})

    def test_old_chainage_keeps_borrow_string(self):
        candidates, notes, questions = self.locator.resolve_old_chainage(
            "K5+240", "借用线", "甲", "乙"
        )
        self.assertEqual({candidate.坐标 for candidate in candidates}, {740})
        self.assertTrue(any("借用线" in candidate.依据 for candidate in candidates))

    def test_segment_borders_are_shared(self):
        self.assertEqual(
            sorted(self.locator.segments_at("正线", "乙", 500)), ["S1", "S2"]
        )


class FactSecretTests(Base):
    def test_raw_sentence_is_kept(self):
        fact = self.fact("B5", "断链")
        self.assertIn("暂不能按旧线桩号解释风积沙范围", fact.句子)
        self.assertEqual(fact.句子, fact.句子.strip())

    def test_old_chainage_reading_does_not_change_version(self):
        fact = self.fact("B5", "断链")
        self.assertEqual(fact.版本, "乙")

    def test_segment_header_is_a_question(self):
        fact = self.fact("B4", "未见")
        self.assertTrue(any("标段" in q for q in fact.定位问题))

    def test_reply_reference_is_recorded(self):
        fact = self.fact("B2", "北侧路肩")
        self.assertIn("B1", fact.引用)


class DesignLogTests(Base):
    def test_design_log_only_covers_written_scope(self):
        e6 = self.design.by_id["E6"]
        self.assertEqual((e6.低, e6.高), (100, 300))
        self.assertEqual(e6.单元, ["主线北半幅"])
        self.assertIn("北肩", e6.未签)
        self.assertTrue(e6.结论.startswith("部分签核"))

    def test_send_for_review_is_not_signoff(self):
        e1 = self.design.by_id["E1"]
        self.assertEqual(e1.结论, "送审记录（非签核）")

    def test_pending_review_is_not_closed(self):
        e3 = self.design.by_id["E3"]
        self.assertTrue(e3.待办)
        self.assertEqual(e3.结论, "回复未闭合（待后续）")

    def test_reference_is_not_joint_signoff(self):
        e7 = self.design.by_id["E7"]
        self.assertIn("不表示会签", e7.结论)
        self.assertIn("拟引用西段交底", e7.引用)

    def test_boundary_only_signoff_is_not_whole_segment(self):
        e4 = self.design.by_id["E4"]
        self.assertTrue(e4.边界点)
        self.assertTrue(any("接合" in note for note in e4.备注))

    def test_old_chainage_string_resolves_once(self):
        e8 = self.design.by_id["E8"]
        self.assertEqual((e8.低, e8.高), (500, 740))


class AssessmentTests(Base):
    def test_unknown_status_when_only_submitted(self):
        result = self.assessment.drawing("G6")
        self.assertEqual(result["状态"], "待确认")
        self.assertEqual(result["签核"], [])

    def test_partial_signoff_without_clean_range_says_so(self):
        # G1 的 B1 初查发现未写横向，不能据此把主线北半幅 100–300 留作有效签核。
        result = self.assessment.drawing("G1")
        self.assertEqual(result["状态"], "部分待确认")
        self.assertIn("没有可保留的签核区间", result["摘要"])

    def test_preserved_range_excludes_only_affected_part(self):
        corpus = Corpus(ROOT)
        locator = Locator(corpus)
        builder = FactBuilder(corpus, locator)
        design = DesignLog(corpus, locator)
        # 把未写横向的初查事实收窄到北肩 300–400，其余签核应可保留。
        facts = builder.all_facts()
        for fact in facts:
            if fact.letter_id == "B1" and "小岬" in fact.小句:
                fact.单元 = ["北肩"]
                fact.横向 = ["北肩"]
                fact.起坐标, fact.止坐标 = 300, 400
        builder.all_facts = lambda: facts
        assessment = Assessment(corpus, locator, builder, design)
        result = assessment.drawing("G1")
        self.assertEqual(result["状态"], "部分待确认")
        self.assertIn("其余签核仍有效", result["摘要"])
        self.assertIn("主线北半幅", result["摘要"])

    def test_unsigned_drawing_is_held(self):
        result = self.assessment.drawing("G2")
        self.assertEqual(result["状态"], "挂起")

    def test_other_version_fact_is_not_applied(self):
        result = self.assessment.drawing("G2")
        b3 = [row for row in result["事实"] if row["事实"] == "B3"][0]
        self.assertFalse(b3["适用"])
        self.assertIn("甲版", b3["不适用原因"])

    def test_survey_and_review_are_separate_needs(self):
        result = self.assessment.drawing("G3")
        labels = {(row["类别"], row["标的"]) for row in result["需求"]}
        self.assertIn(("调查", "试坑记录"), labels)
        self.assertIn(("复核", "相符版次横断面复核"), labels)

    def test_pending_section_review_blocks(self):
        result = self.assessment.drawing("G3")
        texts = " ".join(row["说明"] for row in result["需求"])
        self.assertIn("未闭合", texts)

    def test_dependency_reminder_is_not_blocking(self):
        result = self.assessment.drawing("G3")
        reminders = [row for row in result["挂起"] if row["类别"] == "提醒"]
        self.assertTrue(reminders)
        self.assertTrue(any("G1" in row["原因"] for row in reminders))

    def test_post_survey_change_keeps_other_signoff(self):
        result = self.assessment.drawing("G1")
        changes = [
            row for row in result["挂起"]
            if "签核后补充勘察" in row["原因"]
        ]
        self.assertTrue(changes)
        self.assertEqual(result["状态"], "部分待确认")

    def test_history_snapshot_only_uses_earlier_material(self):
        result = self.assessment.drawing("G1")
        first = result["历史"][0]
        self.assertEqual(first["送审"]["编号"], "E1")
        self.assertTrue(first["当时已见交底"])
        self.assertTrue(all(
            row["日期"] <= first["送审"]["日期"] for row in first["当时已见交底"]
        ))

    def test_submission_relation_points_to_changed_facts(self):
        result = self.assessment.drawing("G1")
        relation = result["送审关系"][0]
        self.assertTrue(relation["后续改变"])
        self.assertIn("转回待确认", relation["关系"])

    def test_boundary_only_fact_leaves_interior_uncovered(self):
        result = self.assessment.drawing("G4")
        self.assertNotEqual(result["状态"], "已签核有效")
        self.assertTrue(any("两端衔接" in row["原因"] for row in result["挂起"]))

    def test_shared_structure_is_not_confirmed_by_coordinate(self):
        shared = self.assessment.shared_structures("G6")
        self.assertTrue(shared)
        self.assertFalse(any(row["可引用"] for row in shared))
        self.assertIn("构筑物编号", shared[0]["说明"])

    def test_junction_reports_unresolved_bridge_evidence(self):
        junction = self.assessment.junction("正线", 500)
        self.assertEqual(junction["状态"], "边界证据归属未定")
        self.assertTrue(junction["待确认归属"])
        self.assertEqual(junction["标段"], ["S1", "S2", "S4"])

    def test_junction_conflict_between_findings(self):
        facts = [
            type("F", (), {"单元": ["北肩"], "性质": "发现"}),
            type("F", (), {"单元": ["北肩"], "性质": "未见（有限范围）"}),
        ]
        conflicts = self.assessment._junction_conflict(facts)
        self.assertTrue(conflicts)

    def test_survey_evidence_is_not_substituted_by_design_note(self):
        result = self.assessment.drawing("G3")
        pits = [
            row for row in result["需求"]
            if row["类别"] == "调查" and row["标的"] == "试坑记录"
        ]
        self.assertTrue(pits)
        joined = "".join(row["说明"] for row in pits)
        self.assertIn("不能代替原始结论", joined)

    def test_questions_keep_candidates(self):
        questions = self.assessment.questions()
        b5 = [row for row in questions if row["来源"] == "B5"]
        self.assertTrue(b5)
        self.assertEqual(len(b5[0]["候选"]), 2)


class TraceTests(Base):
    def test_trace_links_original_to_clause_reply_and_status(self):
        result = self.assessment.drawing("G1")
        trace = [
            row for row in result["事实"]
            if row["事实"] == "B2" and "北侧路肩" in row["原文"]
        ][0]
        self.assertTrue(trace["适用"])
        self.assertIn("P1", trace["条款"])
        self.assertTrue(trace["设计答复"])
        self.assertTrue(any(reply["图号"] == "G3" for reply in trace["设计答复"]))
        self.assertIn(result["状态"], {"部分待确认", "挂起"})

    def test_touching_point_is_not_counted_as_overlap(self):
        # B1 的“桥外向界石”是 500–800；G1 只到 500，只在端点相接。
        result = self.assessment.drawing("G1")
        self.assertFalse(any("界石" in row["原文"] for row in result["事实"]))

    def test_trace_range_is_clipped_to_drawing(self):
        import copy
        fact = self.fact("B2", "北侧路肩")
        wide = copy.deepcopy(fact)
        wide.起坐标, wide.止坐标 = 100, 800
        drawing = self.assessment.drawings["G1"]
        trace = self.assessment._trace_fact(
            drawing, self.assessment.drawing_scope("G1"), wide
        )
        self.assertEqual(trace.范围, (100, 500))
        self.assertTrue(any("相交段" in note for note in trace.备注))

    def test_trace_records_original_sentence(self):
        result = self.assessment.drawing("G1")
        for trace in result["事实"]:
            self.assertTrue(trace["原文"])
            self.assertTrue(trace["事实"])

    def test_design_reply_scope_is_respected(self):
        result = self.assessment.drawing("G1")
        trace = [
            row for row in result["事实"]
            if row["事实"] == "B2" and "北侧路肩" in row["原文"]
        ][0]
        g3 = [row for row in trace["设计答复"] if row["编号"] == "E3"][0]
        self.assertEqual(g3["结论"], "回复未闭合（待后续）")


class EdgeCaseTests(Base):
    def test_dependency_cycle_does_not_recurse_forever(self):
        import copy
        corpus = Corpus(ROOT)
        locator = Locator(corpus)
        builder = FactBuilder(corpus, locator)
        design = DesignLog(corpus, locator)
        corpus.raw["drawings"] = copy.deepcopy(corpus.raw["drawings"])
        for row in corpus.raw["drawings"]:
            if row["id"] == "G1":
                row["依赖"] = ["G3"]
            if row["id"] == "G3":
                row["依赖"] = ["G1"]
        assessment = Assessment(corpus, locator, builder, design)
        result = assessment.drawing("G1")
        self.assertIn(result["状态"], {"部分待确认", "待确认"})

    def test_shared_structure_kind_distinguishes_landmark(self):
        rows = self.assessment.shared_structures("G5")
        kinds = {row["交接"]: row["对象类型"] for row in rows}
        self.assertEqual(kinds["X1"], "构筑物")
        self.assertEqual(kinds["X3"], "位置标志")
        x3 = [row for row in rows if row["交接"] == "X3"][0]
        self.assertFalse(x3["可引用"])
        self.assertIn("标志", x3["说明"])

    def test_structure_without_geometry_is_not_citable(self):
        link = [row for row in self.corpus.rows("crosswalk") if row["id"] == "X1"][0]
        self.assertFalse(self.assessment._structure_confirmed(link))

    def test_borrow_line_bridge_question_is_asked(self):
        fact = self.fact("B4", "未见")
        self.assertTrue(any("桥界面" in q for q in fact.定位问题))

    def test_unlocated_fact_shows_in_drawing_trace(self):
        result = self.assessment.drawing("G2")
        b4 = [row for row in result["事实"]
              if row["事实"] == "B4" and not row["适用"]]
        self.assertTrue(b4)
        self.assertIn("确认归属前不能算作本图证据", b4[0]["不适用原因"])


class LineViewTests(Base):
    def test_line_view_keeps_original_sentences(self):
        from briefing.__main__ import cut_by_line
        view = cut_by_line(self.assessment, "正线", 300, 500)
        self.assertTrue(view["事实"])
        sources = {row["id"]: row["内容"] for row in self.corpus.rows("letters")}
        for fact in view["事实"]:
            self.assertIn(fact["原句"], sources[fact["编号"]])

    def test_line_view_lists_drawings_and_junctions(self):
        from briefing.__main__ import cut_by_line
        view = cut_by_line(self.assessment, "正线")
        self.assertEqual({row["图号"] for row in view["图纸"]},
                         {"G1", "G2", "G3", "G5"})
        self.assertTrue(view["接合点"])

    def test_cli_overview_runs(self):
        from briefing.__main__ import main
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["overview"])
        self.assertEqual(code, 0)
        self.assertIn("G1", buffer.getvalue())


class IntegrityTests(Base):
    def test_directive_explanations_match_material(self):
        self.assertEqual(check_integrity(self.corpus), [])

    def test_every_drawing_has_a_status(self):
        rows = self.assessment.overview()
        self.assertEqual({row["图号"] for row in rows}, set(self.assessment.drawings))
        for row in rows:
            self.assertIn(row["状态"], {
                "挂起", "待确认", "部分待确认", "已签核有效", "已签核（附提醒）",
            })

    def test_split_clauses_keep_text(self):
        sentence = "东沟桥附近疑有盐渍，未测。"
        clauses = T.split_clauses(sentence)
        self.assertEqual(clauses, ["东沟桥附近疑有盐渍", "未测。"])


if __name__ == "__main__":
    unittest.main()
