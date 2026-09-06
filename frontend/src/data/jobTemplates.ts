export interface JobTemplate {
  id: string
  icon: string
  title: string
  desc: string
  tag: string
  /** 点击模板后显示在对话区的自然语言任务说明。 */
  request: string
}

/**
 * 前端所有模板入口共用这一份元数据，避免侧边栏和欢迎页出现名称、文案不一致。
 * id 只用于前后端协议，不直接展示给用户。
 */
export const JOB_TEMPLATES: JobTemplate[] = [
  {
    id: 'slurm-first-job',
    icon: '🚀',
    title: '第一个 Slurm 作业',
    desc: '自动生成最小 sbatch 脚本，提交后跟踪状态并读取本次作业输出',
    tag: '入门',
    request:
      '请你帮我生成一个适合初学者的 Slurm 作业脚本，在平台上提交并跟踪它的运行状态。作业完成后，请读取输出，解释常用的 SBATCH 参数，并告诉我这次作业是否成功。',
  },
  {
    id: 'gpu-training',
    icon: '🧠',
    title: '经典 CUDA 向量加法',
    desc: '自动编译并运行经典向量加法，查看 GPU 耗时、带宽和计算误差',
    tag: 'GPU 入门',
    request:
      '请你帮我生成一个经典 CUDA 向量加法程序和对应的 Slurm 脚本，在 A100 GPU 上编译并运行。完成后，请分析 GPU 型号、核函数耗时、显存带宽和计算误差，并判断计算结果是否正确。',
  },
  {
    id: 'fire-growth',
    icon: '🔥',
    title: 't² 火灾增长与喷淋响应',
    desc: '运行经典火灾工程模型，计算热释放、顶棚射流和喷头动作时间',
    tag: '火灾/燃烧',
    request:
      '请你帮我生成一个基于 Python 的经典 t² 火灾增长、Alpert 顶棚射流和喷淋热响应算例，并在平台上提交运行。完成后，请分析热释放速率、火焰高度、顶棚温度、喷头动作时间、燃料消耗和耗氧量，并说明这个模型的适用范围。',
  },
]
