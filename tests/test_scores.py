"""Comportements globaux du score personnel (FEATURES.md, « Le calcul du score »)."""

import pytest

from tospesa.network import Network


def test_unrated_entity_is_neutral():
    net = Network()
    net.rate("me", "friend", 1)
    assert net.score("me", "unknown_restaurant") == 0


def test_doctor_scenario():
    # Délégation : je fais confiance à un accréditeur, qui accrédite des médecins,
    # qui valident un article -> l'article est positif pour moi.
    net = Network()
    net.rate("me", "medical_board", 1)
    for i in range(20):
        net.rate("medical_board", f"doctor{i}", 1)
    net.rate("doctor3", "article", 1)
    net.rate("doctor7", "article", 1)
    assert net.score("me", "article") > 0


def test_sybils_have_no_influence():
    # Faux comptes : une foule de comptes qui se notent entre eux, que personne de mon
    # réseau ne note positivement, ne bouge pas mon score.
    net = Network()
    net.rate("me", "friend", 1)
    net.rate("friend", "article", 1)
    sybils = [f"sybil{i}" for i in range(500)]
    for s in sybils:
        net.rate(s, "me", 1)  # être noté par moi compte, pas l'inverse
        net.rate(s, "article", -1)
        for t in sybils[:10]:
            if t != s:
                net.rate(s, t, 1)
    assert net.score("me", "article") == 1


def test_distrust_is_inherited_through_trust():
    # Propagation : la défiance passe à travers la confiance.
    net = Network()
    net.rate("me", "b", 1)
    net.rate("b", "c", -1)
    assert net.score("me", "c") < 0


def test_nothing_flows_through_distrust():
    # Propagation : rien ne passe à travers la défiance.
    net = Network()
    net.rate("me", "b", -1)
    net.rate("b", "x", 1)
    net.rate("b", "y", -1)
    assert net.score("me", "b") < 0
    assert net.score("me", "x") == 0
    assert net.score("me", "y") == 0


def test_closer_opinions_weigh_more():
    # Propagation : mon avis l'emporte sur celui d'un ami, qui l'emporte sur celui
    # d'un ami d'ami.
    net = Network()
    net.rate("me", "friend", 1)
    net.rate("friend", "friend_of_friend", 1)
    net.rate("friend", "x", 1)
    net.rate("friend_of_friend", "x", -1)
    assert net.score("me", "x") > 0

    net.rate("me", "x", -1)
    assert net.score("me", "x") < 0


def test_scores_are_personal_and_follow_note_changes():
    # Score personnel : deux personnes aux réseaux opposés voient des scores opposés,
    # et retirer un lien de confiance fait disparaître son influence.
    net = Network()
    net.rate("alice", "critic_a", 1)
    net.rate("bob", "critic_b", 1)
    net.rate("critic_a", "book", 1)
    net.rate("critic_b", "book", -1)
    assert net.score("alice", "book") > 0
    assert net.score("bob", "book") < 0

    net.unrate("alice", "critic_a")
    assert net.score("alice", "book") == 0


def test_one_hacked_contact_cannot_dominate():
    # Je fais autant confiance à Carole qu'à Thomas. Le compte de Carole est
    # piraté : il like 1000 faux comptes qui notent le livre -1. Derrière
    # Thomas, une seule personne connaît le livre, plus loin, et le note +1.
    # Le côté de Carole peut peser un peu plus (plus de sources, plus proches),
    # mais pas beaucoup plus.
    net = Network()
    net.rate("me", "carole", 1)
    net.rate("me", "thomas", 1)
    for i in range(1000):
        net.rate("carole", f"sybil{i}", 1)
        net.rate(f"sybil{i}", "book", -1)
    for j in range(10):
        net.rate("thomas", f"friend{j}", 1)
        net.rate(f"friend{j}", "other_book", 1)
    net.rate("friend0", "reader", 1)
    net.rate("reader", "book", 1)
    assert net.score("me", "book") > -0.5


def test_no_echo_toward_viewer():
    # Quand je consulte, rien ne revient vers moi : que mes contacts me fassent
    # confiance en retour ne change pas mon score.
    def build(reciprocal):
        net = Network()
        net.rate("me", "carole", 1)
        net.rate("me", "thomas", 1)
        net.rate("carole", "book", -1)
        net.rate("thomas", "friend", 1)
        net.rate("friend", "book", 1)
        if reciprocal:
            net.rate("carole", "me", 1)
            net.rate("thomas", "me", 1)
            net.rate("friend", "me", 1)
        return net.score("me", "book")

    assert build(reciprocal=True) == pytest.approx(build(reciprocal=False))


def test_find_nearby_raters_from_closest_to_farthest():
    # Pour l'onboarding : trouver les contacts de contacts (de contacts...) qui
    # ont noté l'entité que je suis en train de noter, les plus proches
    # d'abord. Ni les inconnus ni ceux dont je me méfie n'y figurent.
    net = Network()
    net.rate("me", "a", 1)
    net.rate("a", "b", 1)
    net.rate("b", "c", 1)
    net.rate("b", "book", 1)
    net.rate("c", "book", -1)
    net.rate("stranger", "book", 1)
    net.rate("me", "d", -1)
    net.rate("d", "book", 1)
    assert list(net.nearby_raters("me", "book")) == [("b", 2), ("c", 3)]


def test_weight_scales_how_much_a_note_counts():
    # Une note pèse selon sa pondération, qu'elle porte sur l'entité consultée
    # ou sur un contact.
    on_entity = Network()
    on_entity.rate("me", "a", 1)
    on_entity.rate("me", "b", 1)
    on_entity.rate("a", "book", 1)
    on_entity.rate("b", "book", -1, weight=0.1)
    assert on_entity.score("me", "book") == pytest.approx(9 / 11)

    on_contact = Network()
    on_contact.rate("me", "a", 1)
    on_contact.rate("me", "b", 1, weight=0.1)
    on_contact.rate("a", "book", -1)
    on_contact.rate("b", "book", 1)
    assert on_contact.score("me", "book") == pytest.approx(-9 / 11)
