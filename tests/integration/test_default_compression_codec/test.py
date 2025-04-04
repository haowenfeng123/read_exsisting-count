import logging
import random
import string
import time

import pytest

from helpers.cluster import ClickHouseCluster

cluster = ClickHouseCluster(__file__)

node1 = cluster.add_instance(
    "node1",
    main_configs=[
        "configs/default_compression.xml",
        "configs/wide_parts_only.xml",
        "configs/long_names.xml",
    ],
    with_zookeeper=True,
)
node2 = cluster.add_instance(
    "node2",
    main_configs=[
        "configs/default_compression.xml",
        "configs/wide_parts_only.xml",
        "configs/long_names.xml",
    ],
    with_zookeeper=True,
)
node4 = cluster.add_instance("node4")


@pytest.fixture(scope="module")
def start_cluster():
    try:
        cluster.start()

        yield cluster
    finally:
        cluster.shutdown()


def get_compression_codec_byte(node, table_name, part_name):
    data_path = node.query(
        f"SELECT arrayElement(data_paths, 1) FROM system.tables WHERE database='default' AND name='{table_name}'"
    ).strip()
    cmd = f"tail -c +17 {data_path}/{part_name}/data1.bin | od -x -N 1 | head -n 1 | awk '{{print $2}}'"
    return node.exec_in_container(["bash", "-c", cmd]).strip()


def get_second_multiple_codec_byte(node, table_name, part_name):
    data_path = node.query(
        f"SELECT arrayElement(data_paths, 1) FROM system.tables WHERE database='default' AND name='{table_name}'"
    ).strip()
    cmd = f"tail -c +17 {data_path}/{part_name}/data1.bin | od -x -j 11 -N 1 | head -n 1 | awk '{{print $2}}'"
    return node.exec_in_container(["bash", "-c", cmd]).strip()


def get_random_string(length):
    return "".join(
        random.choice(string.ascii_uppercase + string.digits) for _ in range(length)
    )


CODECS_MAPPING = {
    "LZ4": "0082",
    "LZ4HC": "0082",  # not an error, same byte
    "ZSTD": "0090",
    "Multiple": "0091",
}


def test_default_codec_single(start_cluster):
    for i, node in enumerate([node1, node2]):
        node.query(
            """
        CREATE TABLE compression_table (
            key UInt64,
            data1 String CODEC(Default)
        ) ENGINE = ReplicatedMergeTree('/t', '{}') ORDER BY tuple() PARTITION BY key;
        """.format(
                i
            )
        )

    # ZSTD(10) and ZSTD(10) after merge
    node1.query("INSERT INTO compression_table VALUES (1, 'x')")

    # ZSTD(10) and LZ4HC(10) after merge
    node1.query(
        "INSERT INTO compression_table VALUES (2, '{}')".format(get_random_string(2048))
    )

    # ZSTD(10) and LZ4 after merge
    node1.query(
        "INSERT INTO compression_table VALUES (3, '{}')".format(
            get_random_string(22048)
        )
    )

    node2.query("SYSTEM SYNC REPLICA compression_table", timeout=15)

    # to reload parts
    node1.query("DETACH TABLE compression_table")
    node2.query("DETACH TABLE compression_table")

    node1.query("ATTACH TABLE compression_table")
    node2.query("ATTACH TABLE compression_table")

    node1.query("SYSTEM FLUSH LOGS")
    node2.query("SYSTEM FLUSH LOGS")

    # Same codec for all
    assert (
        get_compression_codec_byte(node1, "compression_table", "1_0_0_0")
        == CODECS_MAPPING["ZSTD"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '1_0_0_0'"
        )
        == "ZSTD(10)\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '1_0_0_0'"
        )
        == "ZSTD(10)\n"
    )

    assert (
        get_compression_codec_byte(node1, "compression_table", "2_0_0_0")
        == CODECS_MAPPING["ZSTD"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '2_0_0_0'"
        )
        == "ZSTD(10)\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '2_0_0_0'"
        )
        == "ZSTD(10)\n"
    )

    assert (
        get_compression_codec_byte(node1, "compression_table", "3_0_0_0")
        == CODECS_MAPPING["ZSTD"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '3_0_0_0'"
        )
        == "ZSTD(10)\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '3_0_0_0'"
        )
        == "ZSTD(10)\n"
    )

    # just to be sure that replication works
    node1.query("OPTIMIZE TABLE compression_table FINAL")

    node2.query("SYSTEM SYNC REPLICA compression_table", timeout=15)

    # to reload parts
    node1.query("DETACH TABLE compression_table")
    node2.query("DETACH TABLE compression_table")

    node1.query("ATTACH TABLE compression_table")
    node2.query("ATTACH TABLE compression_table")

    node1.query("SYSTEM FLUSH LOGS")
    node2.query("SYSTEM FLUSH LOGS")

    assert (
        get_compression_codec_byte(node1, "compression_table", "1_0_0_1")
        == CODECS_MAPPING["ZSTD"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '1_0_0_1'"
        )
        == "ZSTD(10)\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '1_0_0_1'"
        )
        == "ZSTD(10)\n"
    )

    assert (
        get_compression_codec_byte(node1, "compression_table", "2_0_0_1")
        == CODECS_MAPPING["LZ4HC"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '2_0_0_1'"
        )
        == "LZ4HC(5)\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '2_0_0_1'"
        )
        == "LZ4HC(5)\n"
    )

    assert (
        get_compression_codec_byte(node1, "compression_table", "3_0_0_1")
        == CODECS_MAPPING["LZ4"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '3_0_0_1'"
        )
        == "LZ4\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table' and name = '3_0_0_1'"
        )
        == "LZ4\n"
    )

    assert node1.query("SELECT COUNT() FROM compression_table") == "3\n"
    assert node2.query("SELECT COUNT() FROM compression_table") == "3\n"

    node1.query("DROP TABLE compression_table SYNC")
    node2.query("DROP TABLE compression_table SYNC")


def test_default_codec_multiple(start_cluster):
    for i, node in enumerate([node1, node2]):
        node.query(
            """
        CREATE TABLE compression_table_multiple (
            key UInt64,
            data1 String CODEC(NONE, Default)
        ) ENGINE = ReplicatedMergeTree('/d', '{}') ORDER BY tuple() PARTITION BY key;
        """.format(
                i
            ),
            settings={"allow_suspicious_codecs": 1},
        )

    # ZSTD(10) and ZSTD(10) after merge
    node1.query("INSERT INTO compression_table_multiple VALUES (1, 'x')")

    # ZSTD(10) and LZ4HC(10) after merge
    node1.query(
        "INSERT INTO compression_table_multiple VALUES (2, '{}')".format(
            get_random_string(2048)
        )
    )

    # ZSTD(10) and LZ4 after merge
    node1.query(
        "INSERT INTO compression_table_multiple VALUES (3, '{}')".format(
            get_random_string(22048)
        )
    )

    node2.query("SYSTEM SYNC REPLICA compression_table_multiple", timeout=15)

    # Same codec for all
    assert (
        get_compression_codec_byte(node1, "compression_table_multiple", "1_0_0_0")
        == CODECS_MAPPING["Multiple"]
    )
    assert (
        get_second_multiple_codec_byte(node1, "compression_table_multiple", "1_0_0_0")
        == CODECS_MAPPING["ZSTD"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '1_0_0_0'"
        )
        == "ZSTD(10)\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '1_0_0_0'"
        )
        == "ZSTD(10)\n"
    )

    assert (
        get_compression_codec_byte(node1, "compression_table_multiple", "2_0_0_0")
        == CODECS_MAPPING["Multiple"]
    )
    assert (
        get_second_multiple_codec_byte(node1, "compression_table_multiple", "2_0_0_0")
        == CODECS_MAPPING["ZSTD"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '2_0_0_0'"
        )
        == "ZSTD(10)\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '2_0_0_0'"
        )
        == "ZSTD(10)\n"
    )

    assert (
        get_compression_codec_byte(node1, "compression_table_multiple", "3_0_0_0")
        == CODECS_MAPPING["Multiple"]
    )
    assert (
        get_second_multiple_codec_byte(node1, "compression_table_multiple", "3_0_0_0")
        == CODECS_MAPPING["ZSTD"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '3_0_0_0'"
        )
        == "ZSTD(10)\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '3_0_0_0'"
        )
        == "ZSTD(10)\n"
    )

    node2.query("SYSTEM SYNC REPLICA compression_table_multiple", timeout=15)

    node1.query("OPTIMIZE TABLE compression_table_multiple FINAL")

    node2.query("SYSTEM SYNC REPLICA compression_table_multiple", timeout=15)

    assert (
        get_compression_codec_byte(node1, "compression_table_multiple", "1_0_0_1")
        == CODECS_MAPPING["Multiple"]
    )
    assert (
        get_second_multiple_codec_byte(node1, "compression_table_multiple", "1_0_0_1")
        == CODECS_MAPPING["ZSTD"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '1_0_0_1'"
        )
        == "ZSTD(10)\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '1_0_0_1'"
        )
        == "ZSTD(10)\n"
    )

    assert (
        get_compression_codec_byte(node1, "compression_table_multiple", "2_0_0_1")
        == CODECS_MAPPING["Multiple"]
    )
    assert (
        get_second_multiple_codec_byte(node1, "compression_table_multiple", "2_0_0_1")
        == CODECS_MAPPING["LZ4HC"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '2_0_0_1'"
        )
        == "LZ4HC(5)\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '2_0_0_1'"
        )
        == "LZ4HC(5)\n"
    )

    assert (
        get_compression_codec_byte(node1, "compression_table_multiple", "3_0_0_1")
        == CODECS_MAPPING["Multiple"]
    )
    assert (
        get_second_multiple_codec_byte(node1, "compression_table_multiple", "3_0_0_1")
        == CODECS_MAPPING["LZ4"]
    )
    assert (
        node1.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '3_0_0_1'"
        )
        == "LZ4\n"
    )
    assert (
        node2.query(
            "SELECT default_compression_codec FROM system.parts WHERE table = 'compression_table_multiple' and name = '3_0_0_1'"
        )
        == "LZ4\n"
    )

    assert node1.query("SELECT COUNT() FROM compression_table_multiple") == "3\n"
    assert node2.query("SELECT COUNT() FROM compression_table_multiple") == "3\n"

    node1.query("DROP TABLE compression_table_multiple SYNC")
    node2.query("DROP TABLE compression_table_multiple SYNC")


def test_default_codec_for_compact_parts(start_cluster):
    node4.query(
        """
    CREATE TABLE compact_parts_table (
        key UInt64,
        data String
    )
    ENGINE MergeTree ORDER BY tuple()
    """
    )

    node4.query("INSERT INTO compact_parts_table VALUES (1, 'Hello world')")
    assert node4.query("SELECT COUNT() FROM compact_parts_table") == "1\n"

    node4.query("ALTER TABLE compact_parts_table DETACH PART 'all_1_1_0'")

    data_path = node4.query(
        f"SELECT arrayElement(data_paths, 1) FROM system.tables WHERE database='default' AND name='compact_parts_table'"
    ).strip()
    node4.exec_in_container(
        [
            "bash",
            "-c",
            f"rm {data_path}detached/all_1_1_0/default_compression_codec.txt",
        ]
    )

    node4.query("ALTER TABLE compact_parts_table ATTACH PART 'all_1_1_0'")

    assert node4.query("SELECT COUNT() FROM compact_parts_table") == "1\n"

    node4.query("DETACH TABLE compact_parts_table")
    node4.query("ATTACH TABLE compact_parts_table")

    assert node4.query("SELECT COUNT() FROM compact_parts_table") == "1\n"
    node4.query("DROP TABLE compact_parts_table SYNC")
