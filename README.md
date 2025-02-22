效果：该脚本调用谷歌gemini的api，实现中文对其他语言文本的快速全自动翻译，翻译后会自动校对，并输出最终结果。

程序用法：

1.安装python环境。

2.安装程序所需要的所有库。

3.解压程序文件包，把gemini文件夹放在D盘根目录下。任何路径和文件夹名称的改变都需要在main.py文件中作全局修改，很麻烦，建议别改。

4.用编辑器（如visual studio code）打开geminitranslator文件夹下的main.py文件，在API_KEY、API_URL、PROXY三处分别填写谷歌gemini的API、url、代理地址。确保api有额度。代理最好是稳定的美国ip。

5.直接双击运行start.bat文件，或在python编辑器中运行main.py。此时会弹出终端信息，运行的所有过程、报错都会在终端显示，终端全程必须保持打开状态，万不可关闭。

6.在弹出的页面上手动填写待翻译文件的本地地址、希望翻译的风格（用自然语言填写，填多少都行，但太长会浪费api。基本的设定已经在程序中设置好了。）温度变量（控制输出结果的自由度，学术论文等推荐0.7左右，文学作品可以1.0，太低或太高都会影响结果质量）。

7.点击“开始翻译”。翻译过程中可以暂停。暂停后可以继续。程序意外崩溃或关闭后，下次启动时可以继续进行上次未完成的工作。确定整个文档翻译并校对完成后，请手动按下停止键。如果在翻译或校对过程中按下停止，工作将无法恢复。

8.完成的结果输出为txt文件，自动保存在gemini-translated文件夹下。此文件夹下的temp文件夹中为保存翻译进度的文件，包括翻译进度（txt、jason）、到目前为止翻译的结果（txt），不要轻易删除，否则中途终止程序后下次没法接着翻译。

9.本程序暂时只支持单个工程。如果有多个工程，可以自己另外建立文件夹保存该工程的所有临时文件，不与其他工程混淆。在下次需要继续某项工程时，手动复制到genimi-translated/temp文件夹下，重启程序令其被识别出来。


注意：

1.gmini的模型有每日限额。不同模型限额不同。达到上限后，api五次请求失败后程序就会自动停止。等限额恢复后再启动程序，可继续上次的工作。所以理论上只要有额度，多长的文本都能译完。如果你狠富裕，你可以开通付费api，额度和速度都会很大提高。

2.如果一个大文档在翻译阶段显示的段落数极少，说明这个文档的分段法有问题，本程序可能无法正确给它分段，输出结果将不理想。本程序使用的分段法是以换行符分段，故若遇到这个问题，请自行调整文档。

3.脚本中的chunk_size和batch_size分别控制的是校对和翻译阶段向gemini单次上传的总段落数，都可以自行调整。默认值是经过测试不会超出单次api申请限额的数值，这个数值下完成一部长篇小说的翻译和校对可能需要十几分钟。数值过大可能导致api申请超限，报429错误。不同模型的每日申请限额请自行查找。

4.如果工作中点击了暂停，请务必等看到终端显示临时文件都已存入temp文件夹再点击继续，这个过程有点慢，请耐心等待数秒。

4.对硬件的要求：

最低配置：

CPU：Intel i5 或 AMD Ryzen 5

内存：8GB

硬盘：10GB 可用空间

稳定的网络连接

推荐配置：

CPU：Intel i7 或 AMD Ryzen 7 (或更高)

内存：16GB 或更多

硬盘：50GB 或更多可用空间 (SSD 更好)

更快的网络连接

