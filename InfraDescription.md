有一个数据管线，其核心是个PostgreSQL数据库，里面记录多个lerobot数据集的信息，一个dataset_name对应一个数据集，一个数据集包含一个或多个lerobot文件，这些文件真实存储在硬盘中，物理上一个lerobot文件实际上是一个目录内含多个子目录和文件，逻辑上一个lerobot文件包含一个或多个episodes。设计中管线的各个步骤通过数据库的status表里每行各列来做生产者-消费者的同步队列，status_statistic表只有一行提供各个步骤的完成量的统计，data表盛放具体的数据。还有版本相关的几个表来做数据处理的版本管理

版本管理的逻辑是，数据管线有多个步骤，每个步骤可能会做多个版本的处理，每个版本的处理可能生产多列信息写在data表里。用户创建版本时需要选择该版本从属于哪个步骤，以及指定版本号，给定该版本需要往data表里写入哪些新列。

通常来说，用户创建版本后，会跑该版本的数据处理步骤，处理步骤会往data表里该版本创建的列写入处理结果，一个lerobot处理结束后，会把status表里该lerobot行该版本key的列置为True这样后续的步骤如果对此步骤有依赖就能获取到这个状态从而开始后续处理步骤，同时还会把status_statistic表里唯一一行该版本key的值+1，表示多了一个lerobot完成了该版本的处理

数据库的连接信息写在DBInfo.json，有两个库，production_env是真实的库，test_env是production_env的一个测试环境。两个库均还有个reader用户只有只读权限，密码也是postgres

设计里的数据管线有多个表格，以下是这些表及其列

status表: 每行是一个lerobot，大部分的列是BOOL值，表征该lerobot的某个处理是否完成
    lerobot_uuid 类型是UUID，数据库里所有UUID列的类型都是UUID不是TEXT
    create_time
    dataset_name 数据集名，多个lerobot会同属一个数据集
    lerobot_path
    download_finish BOOL
    register_finish BOOL
    s_{step_key1}_v_{version_no1} BOOL
    s_{step_key2}_v_{version_no2} BOOL
    ...

lerobot_episode表:  记录lerobot的一些信息以及从lerobot到episode的映射
    lerobot_uuid
    create_time
    lerobot_path lerobot文件在磁盘上的真实路径
    episode_count 该lerobot内有多少个episode
    episodes_uuid_list 该lerobot的uuid的列表，该值一般非常长
    lerobot_meta lerobot文件的元信息
    dataset_name
    top_cam_name lerobot的头部相机文件夹名
    left_cam_name lerobot的左手相机文件夹名
    right_cam_name lerobot的右手相机文件夹名
    cam_name_list lerobot有的相机文件夹名列表

status_statistic表: 只有一行，里面是count，记录每个步骤的完成数（多少个lerobot完成了某个步骤）
    download_finish
    register_finish
    s_{step_key1}_v_{version_no1}
    s_{step_key2}_v_{version_no2}
    ...

data表： 规模巨大，每一行是一个episode
    episode_uuid
    create_time
    lerobot_uuid 所属的lerobot的uuid，反向能对应回status和lerobot_episode表里的lerobot_uuid
    lerobot_path 所属的lerobot文件路径
    in_lerobot_episode_id 该episode在其所属lerobot内是第几个episode
    dataset_name
    origin_video_fps  该episode的视频原始fps
    origin_video_duration_s 该episode的视频原始时长，单位秒
    origin_video_frame_count 该episode的视频原始帧数
    has_wrist_cam  该episode是否有腕部相机
    has_body 该episode是否有身体数据
    dataset_type 数据集类型（Ego, 真机，仿真等）
    s_{step_key1}_v_{version_no1}_c_{aaa}
    s_{step_key1}_v_{version_no2}_c_{bbb}
    s_{step_key2}_v_{version_no1}_c_{ccc}
    s_{step_key2}_v_{version_no2}_c_{ddd}
    ...

step_key表  步骤键的表，因为每个版本创建时会让用户生成列，用户自己给定列名比如aaa，真正插入数据库里的列名是s_{step_key}_v_{version_no}_c_{aaa}，避免数据库里列名冲突。这里的step_key用于区分不同的步骤
    step_key
    create_time
    step_explain  step_key的注释，创建step_key时可选写入

version_info表  记录版本信息的表，每行是一个版本信息
    version_key  版本key，格式是s_{step_key}_v_{version_no}
    version_no  版本号，由用户自己给定
    step_key  版本所属的step_key
    version_note  版本的注释，可选写入
    create_time
    created_data_columns  该版本会往data表里写入哪些列（这里记录的是用户给的原始列名列表比如[aaa, bbb]，但实际data表里的列名是s_{step_key}_v_{version_no}_c_{aaa}, s_{step_key}_v_{version_no}_c_{bbb}）

column_info表   记录每个列的信息
    column_key  s_{step_key}_v_{version_no}_c_{aaa}类似这样，data表里真正的列名
    column_name  aaa类似这样，用户给的列名
    step_key  这列属于哪个step
    version_no  这列属于哪个版本号version_no
    column_note  这列的注释，默认是[{data_type}] {用户输入的注释}，用户就算不输入注释，也会自动生成[{data_type}]前缀
    create_time