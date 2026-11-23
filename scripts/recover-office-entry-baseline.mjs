// Restore only the already-installed R2 client source before adding one menu entry.
// No build, installer, network request, server change, application or profile access.
import fs from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import * as asar from "@electron/asar";

const PINNED = {
  "snapshotAt": "2026-11-03T09:22:23.832Z",
  "formalRoot": "D:\\maiyi\\SignalDesk-源码交接-2026-09-16\\SignalDesk-源码交接\\SignalDesk",
  "candidateRoot": "D:/maiyi/tmp/health-push-064-src",
  "installedAsar": "D:/maiyi/signaldesk-desktop/resources/app.asar",
  "installedAsarSha256": "74641bc54e68a6b92fffa14bb0752a548a23dd8cd4a32cc0eb01e09a8d136235",
  "installedR2MainSha256": "a1d946169d81df58ab8bd21f75c566a8664633ff73faa9bbf37e6286df43b4d6",
  "formal": {
    "electron/desktop-updater.cjs": "57ed06e55db9da0510a6a5b98c812284e1635e45258068feedc227f6f2983ed8",
    "electron/knowledge-watch.cjs": "e5d582850ae1fc4a298ebe0738b5a029b0716dbb9ba36ef26bb98c541475420a",
    "electron/main.cjs": "0e14662d62abb3e6d1e51a6b668dbe3fced52caecfa9fad03e045d52a2b516a4",
    "electron/preload.cjs": "1dc68f57557c54f376d3979e78f478dcc89675513e21880d9fd6aad3301795c1",
    "electron/video-desktop.cjs": "c3cd5b2985c3c3c0782ec3f832cd03c9137a96c1a918a3bbb03704ac28e82386",
    "index.html": "3f740ebdce8e58124fe81a60af8ddd0394da5e1da7c2560ab18ba3c533d55636",
    "package-lock.json": "007f9738506453735c0ec0a586958cd84650710add1e6234e9c078429f262f5a",
    "package.json": "4dda5439ea9661e100dc46be0c93a4efccebf76ac936c0b893174ef603d47a8a",
    "src/App.test.tsx": "dd3cf6c841df8470b0fefdce82726f44035a41e3cf3b6a9a9ce35104080612e6",
    "src/App.tsx": "bf1b0ecebc0882d498bf0b544fac28cfbd985740da66eb9a226f02e99b32308a",
    "src/assets/content-health-cover.png": "e7f1e77fbf2573abdc674068fffd8ade329aec36bbb45f6364d8f6da47518364",
    "src/assets/content-health-cover.webp": "9b464f075a60fb06306402b58cd64c1012c0df922dbde3a18deab7d2a829d142",
    "src/assets/mayi-logo.png": "21074c1f5ea45c402a52923e840b850d9c82daa04136a8475d036d789c58b7f8",
    "src/assets/mayi-logo.webp": "3258f35fe628ed10f7a697501f10166b05ad09d5f91626a8391cca1f9815940a",
    "src/assets/mayi-symbol.png": "9c8b9ce942d43f5fdc4e0b2a03f28e8de87d1b5bc2fa4e599643b10ea9bb653e",
    "src/components/ChannelDialog.tsx": "bef3e2b79d0581603806ca1234cdbbbea2720e6ab89e5f825fbd93737980d9cf",
    "src/components/ChannelsView.tsx": "98940d87a930891516e9a88fc6104921c567ac60253ba1109fd2f2e2518f8045",
    "src/components/content/content-studio.css": "0729037fbc52630120c44d579a88c6d373894e1735887396bc58284047754ed5",
    "src/components/content/ContentFields.tsx": "54a2f5cdd525e997fb69034f0c56ba3e17b9a508b3cb68d1cd90d36cc595b44d",
    "src/components/content/ContentStudio.test.tsx": "a7c66faa53ba39bed58f93a8d1b28cbfd0d77e94f188b3618525dddf156260a0",
    "src/components/content/ContentStudio.tsx": "184867b3c1b67af1e5091ce9df17415e3db28a33021876e5782d0c4689ade23f",
    "src/components/ContentGenerationView.test.tsx": "f1c49af1bd1a9363e198e9c2850ba9713a84c0743d754f3a26928f6b2d228cef",
    "src/components/ContentGenerationView.tsx": "87505ef30d0b995a37fdb8d04b87bee6af9a7264dff39fc29f0e4099dcb71105",
    "src/components/knowledge-workspace.css": "e6472111f4fcc068d8561f18caaf3ab78ec1478052078a9d18494b287e69a55b",
    "src/components/KnowledgeAsr.test.tsx": "ca4a61009e27701546a4af7b9cd82c89ee7459c91a441f65c75d635077aae035",
    "src/components/KnowledgeAsr.tsx": "aca2e5699c87bb4dc078c8bd1d7aebe908294bdede95c93dacd9c5acfcfaf29c",
    "src/components/KnowledgeCosts.tsx": "3ba28e0923e7b4260df29ee3433eed0111225706d8216b84a6be02ff7d9ec516",
    "src/components/KnowledgeFolderWatch.tsx": "98c7a783e7ba94f82a54ec35e862dd0eaa0b31e83f2fc99b3858368ab5400411",
    "src/components/KnowledgeHistory.test.tsx": "985136a0a1cbe0ebab531a59d6d248133e03f3a794a2aa7a62f7f28de8d3d5c1",
    "src/components/KnowledgeHistory.tsx": "7bd6a8c0292a744ae263b1f9897a083ceb3749e4f38dd59fb4d62b96f7304826",
    "src/components/KnowledgeLinkImport.tsx": "9311f5f583c8b02fd0e6d5be07d8333eb8621de68d7f5d56b7113b86ee56708b",
    "src/components/KnowledgeMediaMerge.test.tsx": "6ac0524d687667ba52621f58be03958ca66c4ac409d19123f4180cd0ddcb8fed",
    "src/components/KnowledgeMediaMerge.tsx": "6e8149f5fa133c5374e5f215c2601d87d837693202846d6a7825d07962799e36",
    "src/components/KnowledgeNotificationListener.tsx": "1264a34b565792a2a3a7601fb54d009172776b04baa092eea8ab87bb4ded5f1a",
    "src/components/KnowledgeNotifications.tsx": "c9e5a6080eb6afa76447b575fa99649cb960900c1a4dc6f0a7ad59ad3406ce3e",
    "src/components/KnowledgeOcr.tsx": "5d56cb1f681c9330a73a0768e2687456ae77161a4b55c87cb7b15e3df376df6e",
    "src/components/KnowledgeOperations.tsx": "64b45a23f565875e6ea5ab656523119147a57b770c397b5c6b6471bda6443d2d",
    "src/components/KnowledgeOrganization.tsx": "651f7367052dc9e6451f4e9c87ed496adb9e24aebf8e5a3bdf7719831593763d",
    "src/components/KnowledgeRemainingMedia.test.tsx": "3530eb9895b3bfe3794762abff0b95b7a6464a4538bdb705617a09f000a5e1f3",
    "src/components/KnowledgeRemainingServices.test.tsx": "95e0703101434baa9928bcc8838adfbfd7b63d579b43bb2b7a0d5b930d7cb89e",
    "src/components/KnowledgeRemainingTeams.test.tsx": "2feaf9e018904d1d5524f04eb1e6f444dadad29c58391632b7f6cd56c8b84334",
    "src/components/KnowledgeRemainingWorkspace.test.tsx": "981c2ca3c37d22e0996547db3bf8eb7fce43948edae7933aa8248c654b87dfa8",
    "src/components/KnowledgeSemantic.tsx": "e9e15e88346af593e7b604009fb5cd44292e684fafa07dcb4720afbb827545c5",
    "src/components/KnowledgeSemanticBatch.test.tsx": "8c90b39037d56402f70aea66e8ed6c5cede5210caf803d76ad3a32b8c6add093",
    "src/components/KnowledgeSemanticBatch.tsx": "cdbf3adbd095242799aa7671d64a968343c3642ee11f277f1258f6eef30695a7",
    "src/components/KnowledgeShare.tsx": "98a77915d01bd9b3dd6517afc9946deaf68b989836fbb4d59e4abffd761e4645",
    "src/components/KnowledgeTeams.test.tsx": "a12b208981772e541b27bb4b6ee670e20bbd8e89e0e8122e750334ac38f48a16",
    "src/components/KnowledgeTeams.tsx": "b5b0807c7111aa2c6861656ddc185e957d881aa59e67e5067a8704d27a8bdd23",
    "src/components/KnowledgeUsage.tsx": "4eef234d1486c1526100a9cb2c3edc86095084095923c99ed7bad31438bc9494",
    "src/components/KnowledgeVideoOcr.tsx": "fe08877993c9d41b3590b74f0e01a91167f288121890c75f910b67cfd0dccef5",
    "src/components/KnowledgeWorkspace.test.tsx": "9b88027dfc2084f4703f86fe338934884cf83f6f7f05e609f0f0c4e2b7c2ad83",
    "src/components/KnowledgeWorkspace.tsx": "b89c5499d12626a6666a2c143090fb3652d37cd315f3fbfa3c090267f89201b4",
    "src/components/LoginView.tsx": "d4f2a495b30a41923953a903959f45252bc523a3a4b15d43276127beeedef03a",
    "src/components/member-management.css": "2a0444a71608b1bae0129668fb413fe075678762d35536b30271239091bf27d6",
    "src/components/MemberManagement.test.tsx": "ea68f671fb86178ff5f711ff103d4821f48c8ddbc56f6679136596f8065b3ed7",
    "src/components/MemberManagement.tsx": "67d32ecf5883232465fa34f5a11aed4db77a4457b3b6fb94bb57e03f3e00d8b3",
    "src/components/MessageWorkspace.tsx": "1d88c7c124acee7f3be847f13bb25af2e51a57f285978ab55b8042787ab113f1",
    "src/components/SettingsView.tsx": "ab48b577ee9c40bfbb6065a620d2acf6b42fcc7ef9703f14b0a3f5c1e64e477a",
    "src/components/Sidebar.tsx": "e5acd4c263a78e190c2432eda9db943a9fb4c12438c34e795c7d0ba6382dcac4",
    "src/components/TaskDialog.tsx": "fc3d21a0dd774fef0a521e21eeb6f7063b03e9802c21037bc3975cb238ce2c07",
    "src/components/TaskWidget.test.tsx": "3ae2d785c32edc43bc47dab520868c96f7fc553edfb75f2bcdb0744db81b4e4d",
    "src/components/TaskWidget.tsx": "334ae3850ad88940aeac8c8b581e6f5d3c08723414120af7579f8fd0c22966d1",
    "src/components/TaskWorkspace.test.tsx": "40f5ec0346cd5f05d0f97fe329a1d22820b7c12a1178290ab622928a9d3c0d0d",
    "src/components/TaskWorkspace.tsx": "88639d59548c4e2b89a7dcabf65100f64aecce0020d5ed482e4b2f66aaad95c9",
    "src/components/Toast.tsx": "2a329b7e110a7d65bb94443d7a4de4fcecef254e99cddec407eb5afa2e1f3e48",
    "src/components/Topbar.tsx": "9097f4d9a290d84cf8ffa70b428d11b96d49d939c21853d91bfd098c8c295123",
    "src/components/VideoGenerationWorkspace.css": "731518d0079e1580ea25a773aef2fd2c2ea45d07de63284d5d11046f8647c0ac",
    "src/components/VideoGenerationWorkspace.test.tsx": "a73ffd296ecd2bef9682feb7de43109852976ff16b7ebfa87c5a5fdbe4ce7da6",
    "src/components/VideoGenerationWorkspace.tsx": "c8d140deb2049b4fed40475ab00151158c00b07daec8dafb9e4ec9e865bf9eb2",
    "src/data/demo.ts": "3fdc2ac57d082b911b274498809b2cbe5498360933c4d4c14c1cd24d39969cfb",
    "src/lib/articleApi.test.ts": "b84a1ab05aa1d5840b4d5d66ede0f9af8058879ceb2747890b73ac1880746e34",
    "src/lib/articleApi.ts": "74fb9d0bce710e5243e696756aeb52271a7076317d22b9376c1ae81226797959",
    "src/lib/authApi.test.ts": "dd442c1fae56190df470b3b70d4a6cc58ad9388f96dd663539cce48a1850e0ef",
    "src/lib/authApi.ts": "eec707726692f671338c15e2d70afeea47bb612af2a335965e8c5dcaeda5ebcc",
    "src/lib/channelRules.test.ts": "a0755e572c0efe0596eda1d0b628f9a048ade94b53e18c973c0bc7422b50aa82",
    "src/lib/channelRules.ts": "ab1dc3d67920e22cc4f78f0ae86127d358486cfc898329d40d3a1c3cbe0596e9",
    "src/lib/contentApi.ts": "cf0e95fbda228472f8602d423f1c85d068b10e4d078c44a41a0e6895a5e31ada",
    "src/lib/knowledgeApi.ts": "2cb4d1755784dc9e338709f76477459feb3416f95e983ad5048b469e47005a54",
    "src/lib/memberApi.ts": "4b17c580b8d353abb2bb0445bdb993053c4961633062452ce653b530f7147937",
    "src/lib/ntfy.test.ts": "4b880fbe140bdd566502442edf7037db792318368d3df2a7dd9d681f7714d016",
    "src/lib/ntfy.ts": "0e3db798980d308d520704a62724994a4b0c04c4c58f576b883d6a4e75815214",
    "src/lib/storage.ts": "6b4686536decaffc9b5692d6828c7bfb72b1fd1d66fc8cc9b48d3bc97268b1b4",
    "src/lib/taskApi.ts": "ead667b0a5e5f0df764be9a7259f9ee7f87dcfb825f4539c070bf3b70a9a05ee",
    "src/lib/trash.test.ts": "1a5b2fd93e76001aa36d5c4363feaecb233487572e44596a479dc8117f9de4dc",
    "src/lib/trash.ts": "973188274ebe23292f1272a89d0982b5e3b055b142bb4bae8d6f4b693111dedf",
    "src/lib/videoApi.test.ts": "9da1ca87578ba9b7708cbbef03f2ede35a19050a7f6b23380c68c5787eb3a42e",
    "src/lib/videoApi.ts": "44d45b592ef8e633b59dcd1e571a969571e12482249f187ab692d0f7c2edb7ed",
    "src/main.tsx": "87a64a11893796ef40cb6449ac42dadb6da003f03f28946315299ac78fee8420",
    "src/styles.css": "f447bcf3a6bfb9ae4980a1446a602b7482356f309a9800cfbd943a28fd21b701",
    "src/test/setup.ts": "753debc067c40c65d64599373df56c7896b8859021941ddf5b09bf0ec92e6cd0",
    "src/types.ts": "caaeb9ab9b974171f40c596506221b31eeaede7310f68dbedd518002c1da515c",
    "src/vite-env.d.ts": "c103ba558b370e3840a8e9a12a8f0e08da9f3a491e01c9928fd7b479c6267e13",
    "tsconfig.json": "200957cfd16a713b4846a2b9ab54d19f1518a6951c5a52019f1fe307955b72e5",
    "vite.config.ts": "5ac5710f3eb924ef169bf1f36b3e7a3b2500f22bb5e07b4d7376c264d16c1a4d"
  },
  "candidate": {
    "electron/desktop-updater.cjs": "57ed06e55db9da0510a6a5b98c812284e1635e45258068feedc227f6f2983ed8",
    "electron/knowledge-watch.cjs": "71d658c752e9229f7ec396e0fe65ed8b49c1b12a8c068e459f6ddde0276995b6",
    "electron/main.cjs": "9b0aaa011d79cb78ae5b7d27c88405601c8e5a9db5a34c8a9f394dc6c25ce046",
    "electron/preload.cjs": "1dc68f57557c54f376d3979e78f478dcc89675513e21880d9fd6aad3301795c1",
    "electron/video-desktop.cjs": "c3cd5b2985c3c3c0782ec3f832cd03c9137a96c1a918a3bbb03704ac28e82386",
    "index.html": "3f740ebdce8e58124fe81a60af8ddd0394da5e1da7c2560ab18ba3c533d55636",
    "package-lock.json": "9ac857dcd84e6220f949197d1591153ea18fbc188cf7333d37f8d76aadba1fbe",
    "package.json": "9d5516c0f5d16735a48e6bbf5eddc25cbac969d190d2bdea1db7f9f22c231d22",
    "src/App.test.tsx": "dd3cf6c841df8470b0fefdce82726f44035a41e3cf3b6a9a9ce35104080612e6",
    "src/App.tsx": "b4f977b4a13a065a7acda6d22f87dc71004dff58e68f8262f028b24f76fa59f6",
    "src/assets/content-health-cover.png": "e7f1e77fbf2573abdc674068fffd8ade329aec36bbb45f6364d8f6da47518364",
    "src/assets/content-health-cover.webp": "9b464f075a60fb06306402b58cd64c1012c0df922dbde3a18deab7d2a829d142",
    "src/assets/mayi-logo.png": "21074c1f5ea45c402a52923e840b850d9c82daa04136a8475d036d789c58b7f8",
    "src/assets/mayi-logo.webp": "3258f35fe628ed10f7a697501f10166b05ad09d5f91626a8391cca1f9815940a",
    "src/assets/mayi-symbol.png": "9c8b9ce942d43f5fdc4e0b2a03f28e8de87d1b5bc2fa4e599643b10ea9bb653e",
    "src/components/ChannelDialog.tsx": "5a70236649c9013777252bfe56b630a42e21c3fd044a9754f77ec816f4b19065",
    "src/components/ChannelsView.tsx": "bfb9801c1a0f611980bc801c4f838d9b508ae54a22c6f67240c7b9877e9ad111",
    "src/components/content/content-studio.css": "0729037fbc52630120c44d579a88c6d373894e1735887396bc58284047754ed5",
    "src/components/content/ContentFields.tsx": "54a2f5cdd525e997fb69034f0c56ba3e17b9a508b3cb68d1cd90d36cc595b44d",
    "src/components/content/ContentStudio.test.tsx": "a7c66faa53ba39bed58f93a8d1b28cbfd0d77e94f188b3618525dddf156260a0",
    "src/components/content/ContentStudio.tsx": "184867b3c1b67af1e5091ce9df17415e3db28a33021876e5782d0c4689ade23f",
    "src/components/ContentGenerationView.test.tsx": "f1c49af1bd1a9363e198e9c2850ba9713a84c0743d754f3a26928f6b2d228cef",
    "src/components/ContentGenerationView.tsx": "87505ef30d0b995a37fdb8d04b87bee6af9a7264dff39fc29f0e4099dcb71105",
    "src/components/knowledge-workspace.css": "e6472111f4fcc068d8561f18caaf3ab78ec1478052078a9d18494b287e69a55b",
    "src/components/KnowledgeAsr.test.tsx": "ca4a61009e27701546a4af7b9cd82c89ee7459c91a441f65c75d635077aae035",
    "src/components/KnowledgeAsr.tsx": "11e0a3bd04af741db97a2c28eb61cdac5a811a1fd4964d37416da35976dbea87",
    "src/components/KnowledgeCosts.tsx": "7f4b7d658487016564a05610bc4e56d210decd3a367dd5ada1e7dc7a58e91ddf",
    "src/components/KnowledgeFolderWatch.tsx": "98c7a783e7ba94f82a54ec35e862dd0eaa0b31e83f2fc99b3858368ab5400411",
    "src/components/KnowledgeHistory.test.tsx": "985136a0a1cbe0ebab531a59d6d248133e03f3a794a2aa7a62f7f28de8d3d5c1",
    "src/components/KnowledgeHistory.tsx": "7bd6a8c0292a744ae263b1f9897a083ceb3749e4f38dd59fb4d62b96f7304826",
    "src/components/KnowledgeLinkImport.tsx": "9311f5f583c8b02fd0e6d5be07d8333eb8621de68d7f5d56b7113b86ee56708b",
    "src/components/KnowledgeMediaMerge.test.tsx": "6ac0524d687667ba52621f58be03958ca66c4ac409d19123f4180cd0ddcb8fed",
    "src/components/KnowledgeMediaMerge.tsx": "6e8149f5fa133c5374e5f215c2601d87d837693202846d6a7825d07962799e36",
    "src/components/KnowledgeNotificationListener.tsx": "1264a34b565792a2a3a7601fb54d009172776b04baa092eea8ab87bb4ded5f1a",
    "src/components/KnowledgeNotifications.tsx": "c9e5a6080eb6afa76447b575fa99649cb960900c1a4dc6f0a7ad59ad3406ce3e",
    "src/components/KnowledgeOcr.tsx": "c34e89ce69dd9006be5a082785bbc3213e3bfbc899a6ac58313d59dfeb87a0b4",
    "src/components/KnowledgeOperations.tsx": "64b45a23f565875e6ea5ab656523119147a57b770c397b5c6b6471bda6443d2d",
    "src/components/KnowledgeOrganization.tsx": "651f7367052dc9e6451f4e9c87ed496adb9e24aebf8e5a3bdf7719831593763d",
    "src/components/KnowledgeRemainingMedia.test.tsx": "3530eb9895b3bfe3794762abff0b95b7a6464a4538bdb705617a09f000a5e1f3",
    "src/components/KnowledgeRemainingServices.test.tsx": "95e0703101434baa9928bcc8838adfbfd7b63d579b43bb2b7a0d5b930d7cb89e",
    "src/components/KnowledgeRemainingTeams.test.tsx": "2feaf9e018904d1d5524f04eb1e6f444dadad29c58391632b7f6cd56c8b84334",
    "src/components/KnowledgeRemainingWorkspace.test.tsx": "981c2ca3c37d22e0996547db3bf8eb7fce43948edae7933aa8248c654b87dfa8",
    "src/components/KnowledgeSemantic.tsx": "e9e15e88346af593e7b604009fb5cd44292e684fafa07dcb4720afbb827545c5",
    "src/components/KnowledgeSemanticBatch.test.tsx": "8c90b39037d56402f70aea66e8ed6c5cede5210caf803d76ad3a32b8c6add093",
    "src/components/KnowledgeSemanticBatch.tsx": "cdbf3adbd095242799aa7671d64a968343c3642ee11f277f1258f6eef30695a7",
    "src/components/KnowledgeShare.tsx": "98a77915d01bd9b3dd6517afc9946deaf68b989836fbb4d59e4abffd761e4645",
    "src/components/KnowledgeTeams.test.tsx": "a12b208981772e541b27bb4b6ee670e20bbd8e89e0e8122e750334ac38f48a16",
    "src/components/KnowledgeTeams.tsx": "b5b0807c7111aa2c6861656ddc185e957d881aa59e67e5067a8704d27a8bdd23",
    "src/components/KnowledgeUsage.tsx": "632367c109d914dee0b4bca8e5a7f8217a678c2c16068d26e3469591a5b456d3",
    "src/components/KnowledgeVideoOcr.tsx": "a135e07a19b5c8259bd76cd6ea690a2cdbd21bfecf6f7d6a86db0c4e5150fe65",
    "src/components/KnowledgeWorkspace.test.tsx": "fda9ea3f2fb2c54067a79b0d6521bcf2d49a61482169e2eeb6d618d37de6f65b",
    "src/components/KnowledgeWorkspace.tsx": "e7e611b76ef4d7f2640dc100253ff34129b1e2bce457d7a75f97147e2e58eec0",
    "src/components/LoginView.tsx": "d4f2a495b30a41923953a903959f45252bc523a3a4b15d43276127beeedef03a",
    "src/components/member-management.css": "2a0444a71608b1bae0129668fb413fe075678762d35536b30271239091bf27d6",
    "src/components/MemberManagement.test.tsx": "ea68f671fb86178ff5f711ff103d4821f48c8ddbc56f6679136596f8065b3ed7",
    "src/components/MemberManagement.tsx": "67d32ecf5883232465fa34f5a11aed4db77a4457b3b6fb94bb57e03f3e00d8b3",
    "src/components/MessageWorkspace.tsx": "1d88c7c124acee7f3be847f13bb25af2e51a57f285978ab55b8042787ab113f1",
    "src/components/SettingsView.tsx": "ab48b577ee9c40bfbb6065a620d2acf6b42fcc7ef9703f14b0a3f5c1e64e477a",
    "src/components/Sidebar.tsx": "e5acd4c263a78e190c2432eda9db943a9fb4c12438c34e795c7d0ba6382dcac4",
    "src/components/TaskDialog.tsx": "fc3d21a0dd774fef0a521e21eeb6f7063b03e9802c21037bc3975cb238ce2c07",
    "src/components/TaskWidget.test.tsx": "3ae2d785c32edc43bc47dab520868c96f7fc553edfb75f2bcdb0744db81b4e4d",
    "src/components/TaskWidget.tsx": "334ae3850ad88940aeac8c8b581e6f5d3c08723414120af7579f8fd0c22966d1",
    "src/components/TaskWorkspace.test.tsx": "40f5ec0346cd5f05d0f97fe329a1d22820b7c12a1178290ab622928a9d3c0d0d",
    "src/components/TaskWorkspace.tsx": "88639d59548c4e2b89a7dcabf65100f64aecce0020d5ed482e4b2f66aaad95c9",
    "src/components/Toast.tsx": "2a329b7e110a7d65bb94443d7a4de4fcecef254e99cddec407eb5afa2e1f3e48",
    "src/components/Topbar.tsx": "9097f4d9a290d84cf8ffa70b428d11b96d49d939c21853d91bfd098c8c295123",
    "src/components/VideoGenerationWorkspace.css": "731518d0079e1580ea25a773aef2fd2c2ea45d07de63284d5d11046f8647c0ac",
    "src/components/VideoGenerationWorkspace.test.tsx": "a73ffd296ecd2bef9682feb7de43109852976ff16b7ebfa87c5a5fdbe4ce7da6",
    "src/components/VideoGenerationWorkspace.tsx": "c8d140deb2049b4fed40475ab00151158c00b07daec8dafb9e4ec9e865bf9eb2",
    "src/data/demo.ts": "3fdc2ac57d082b911b274498809b2cbe5498360933c4d4c14c1cd24d39969cfb",
    "src/HealthChannelCompatibility.test.tsx": "886517606740cd7546bc250977b8d36dca3cdd12cfa527e0e80bc44f9ea99552",
    "src/lib/articleApi.test.ts": "b84a1ab05aa1d5840b4d5d66ede0f9af8058879ceb2747890b73ac1880746e34",
    "src/lib/articleApi.ts": "e1bf639c89204f0c0db4919686aef02ab03e82f9622506094562551d25ecf4ea",
    "src/lib/authApi.test.ts": "dd442c1fae56190df470b3b70d4a6cc58ad9388f96dd663539cce48a1850e0ef",
    "src/lib/authApi.ts": "eec707726692f671338c15e2d70afeea47bb612af2a335965e8c5dcaeda5ebcc",
    "src/lib/channelIdentity.test.ts": "3d7be524641577177720b1a220b51cee4de240687706de3181f16e3b3534bd73",
    "src/lib/channelIdentity.ts": "cbf193d048340b2c943d6f0947a6035ff631a63b41030e5a57565c515836c548",
    "src/lib/channelRules.test.ts": "a0755e572c0efe0596eda1d0b628f9a048ade94b53e18c973c0bc7422b50aa82",
    "src/lib/channelRules.ts": "ab1dc3d67920e22cc4f78f0ae86127d358486cfc898329d40d3a1c3cbe0596e9",
    "src/lib/contentApi.ts": "cf0e95fbda228472f8602d423f1c85d068b10e4d078c44a41a0e6895a5e31ada",
    "src/lib/knowledgeApi.ts": "2cb4d1755784dc9e338709f76477459feb3416f95e983ad5048b469e47005a54",
    "src/lib/memberApi.ts": "4b17c580b8d353abb2bb0445bdb993053c4961633062452ce653b530f7147937",
    "src/lib/ntfy.test.ts": "4b880fbe140bdd566502442edf7037db792318368d3df2a7dd9d681f7714d016",
    "src/lib/ntfy.ts": "47d1c47b0794cace3e4dfdd0125618f0a7ff0d084981cb242adf337eeba17502",
    "src/lib/storage.ts": "5d6f930168926f752d6f1084816e4f458e85f9f5ff7faca157fc6c5ac8aefb81",
    "src/lib/taskApi.ts": "ead667b0a5e5f0df764be9a7259f9ee7f87dcfb825f4539c070bf3b70a9a05ee",
    "src/lib/trash.test.ts": "1a5b2fd93e76001aa36d5c4363feaecb233487572e44596a479dc8117f9de4dc",
    "src/lib/trash.ts": "973188274ebe23292f1272a89d0982b5e3b055b142bb4bae8d6f4b693111dedf",
    "src/lib/videoApi.test.ts": "9da1ca87578ba9b7708cbbef03f2ede35a19050a7f6b23380c68c5787eb3a42e",
    "src/lib/videoApi.ts": "44d45b592ef8e633b59dcd1e571a969571e12482249f187ab692d0f7c2edb7ed",
    "src/main.tsx": "87a64a11893796ef40cb6449ac42dadb6da003f03f28946315299ac78fee8420",
    "src/styles.css": "f447bcf3a6bfb9ae4980a1446a602b7482356f309a9800cfbd943a28fd21b701",
    "src/test/setup.ts": "753debc067c40c65d64599373df56c7896b8859021941ddf5b09bf0ec92e6cd0",
    "src/types.ts": "07c2868f7316932965695735dadfac71c0cb666311633a9fbc46a4e7a9841c2a",
    "src/vite-env.d.ts": "c103ba558b370e3840a8e9a12a8f0e08da9f3a491e01c9928fd7b479c6267e13",
    "tsconfig.json": "200957cfd16a713b4846a2b9ab54d19f1518a6951c5a52019f1fe307955b72e5",
    "vite.config.ts": "5ac5710f3eb924ef169bf1f36b3e7a3b2500f22bb5e07b4d7376c264d16c1a4d"
  },
  "dist": {
    "assets/index-B7hIdaqQ.css": "cb9e4bdfac839047d1876ff18fbf961b095334e73bbf6d8077d9745ba258c2cf",
    "assets/index-DPWdinYd.js": "584676d951707d607c1a16a8a260c72a5f874616948b91b768ee4dbb9470507d",
    "assets/knowledgeApi-7HVvWZQi.js": "fa8d9a6520019c792cac6975ae1a470ea683382975a93e53000e9bc7577a8466",
    "assets/KnowledgeNotificationListener-oHVpDkHp.js": "e67e3a86cd01813d8f51eaa0326f6e7886ccd82473c64dba2badde6b6b5b74ce",
    "assets/KnowledgeWorkspace-BKBv5GZT.css": "e214b6ce773441dc5908a67cbcdbf6bc1be960487c7ab1974c15797dc75544c0",
    "assets/KnowledgeWorkspace-BSfhICT4.js": "42358c2bb24fcd217b219f88792a5d2f1153923d256d39e8858d3bb113072d3e",
    "assets/mayi-logo-CUgDQWfw.webp": "3258f35fe628ed10f7a697501f10166b05ad09d5f91626a8391cca1f9815940a",
    "assets/mayi-logo-u3GUNGV8.png": "21074c1f5ea45c402a52923e840b850d9c82daa04136a8475d036d789c58b7f8",
    "index.html": "e5fe01b0c77e3d94c24c119568fc53723da3caa29fa6578ac03f0ca99163e492"
  }
};
const root = path.resolve(fileURLToPath(new URL("../", import.meta.url)));
const candidate = path.resolve(PINNED.candidateRoot);
const installedAsar = path.resolve(PINNED.installedAsar);
const recordRoot = path.join(root, "docs/delivery/internal-management-entry-20261103");
const sourceBackup = path.join(recordRoot, "backups/source-0.6.3");
const distBackup = path.join(recordRoot, "backups/dist-before-recovery");
const sha = bytes => createHash("sha256").update(bytes).digest("hex");
const samePath = (a, b) => path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase();
const runId = new Date().toISOString().replaceAll(/[:.]/g, "-") + "-" + randomUUID().slice(0, 8);
const args = process.argv.slice(2);
assert(args.every(arg => ["--apply", "--dry-run"].includes(arg)), "Only --apply or --dry-run is accepted");
assert(!(args.includes("--apply") && args.includes("--dry-run")), "Choose one mode");
const apply = args.includes("--apply");
assert(samePath(root, PINNED.formalRoot), "Run this script from its pinned formal source project");
const denied = /(^|\/)(?:node_modules|server|hermes-plugin|hermes-skill|artifacts|release|profile|\.local)(\/|$)|(^|\/)(?:\.env(?:\..*)?|auth-session\.json|credentials\.json|.*\.(?:db|sqlite|sqlite3|pem|key|log))$/i;
const selectedRootFiles = ["package.json", "package-lock.json", "index.html", "vite.config.ts", "tsconfig.json"];
function allowedSource(relative) {
  return !relative.includes("\\") && !relative.split("/").includes("..") && !denied.test(relative) &&
    (relative.startsWith("src/") || relative.startsWith("electron/") || selectedRootFiles.includes(relative));
}
function within(base, relative) {
  assert(!path.isAbsolute(relative) && !relative.includes("\0"), "Invalid relative path");
  const target = path.resolve(base, relative), delta = path.relative(base, target);
  assert(delta && !delta.startsWith("..") && !path.isAbsolute(delta), "Path escaped its allowed root");
  return target;
}
async function noLinks(file, missing = false) {
  const absolute = path.resolve(file), parsed = path.parse(absolute);
  let current = parsed.root;
  for (const part of absolute.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    let stat;
    try { stat = await fs.lstat(current); }
    catch (error) { if (missing && error.code === "ENOENT") return; throw error; }
    assert(!stat.isSymbolicLink(), "Refusing a symlink or junction: " + current);
  }
}
async function bytes(file, expected) {
  await noLinks(file);
  const stat = await fs.lstat(file);
  assert(stat.isFile() && stat.nlink === 1, "Expected a regular, unlinked file: " + file);
  const content = await fs.readFile(file);
  if (expected) assert.equal(sha(content), expected, "Pinned file changed: " + file);
  return content;
}
async function exists(file) {
  await noLinks(file, true);
  try { await fs.lstat(file); return true; }
  catch (error) { if (error.code === "ENOENT") return false; throw error; }
}
async function list(base, prefix = "") {
  await noLinks(prefix ? within(base, prefix) : base);
  const result = [];
  for (const item of await fs.readdir(prefix ? within(base, prefix) : base, {withFileTypes: true})) {
    const relative = prefix ? `${prefix}/${item.name}` : item.name;
    assert(!item.isSymbolicLink(), "Directory link refused: " + relative);
    if (item.isDirectory()) result.push(...await list(base, relative));
    else { assert(item.isFile(), "Unexpected directory entry: " + relative); result.push(relative); }
  }
  return result.sort();
}
async function inventory(base) {
  const files = [...await list(base, "src"), ...await list(base, "electron"), ...selectedRootFiles].sort();
  assert(files.every(allowedSource), "Runtime or out-of-scope file in source selection");
  return files;
}
async function verifySource(base, manifest) {
  assert.deepEqual(await inventory(base), Object.keys(manifest).sort(), "Client file set drifted: " + base);
  for (const [relative, hash] of Object.entries(manifest)) await bytes(within(base, relative), hash);
}
async function mkdirSafe(directory) {
  await noLinks(directory, true); await fs.mkdir(directory, {recursive: true}); await noLinks(directory);
}
async function writeNew(file, content) {
  await mkdirSafe(path.dirname(file)); await noLinks(file, true);
  await fs.writeFile(file, content, {flag: "wx", mode: 0o600});
  assert.equal(sha(await bytes(file)), sha(content));
}
async function currentHash(file) { return await exists(file) ? sha(await bytes(file)) : null; }
async function replacePinned(file, content, expectedCurrent) {
  assert.equal(await currentHash(file), expectedCurrent, "Target changed before replacement: " + file);
  const temp = file + ".office-entry-" + runId + ".tmp";
  await writeNew(temp, content);
  try {
    assert.equal(await currentHash(file), expectedCurrent, "Target changed during preparation: " + file);
    await fs.rename(temp, file);
    assert.equal(sha(await bytes(file)), sha(content));
  } finally {
    if (await exists(temp)) {
      assert.equal(sha(await bytes(temp)), sha(content), "Temporary file was modified; retained");
      await fs.unlink(temp); // Only this invocation's exact owned temporary leaf.
    }
  }
}
let lock, lockFile, reportFile;
const touched = [], rollback = [], buffers = new Map();
let plan;
try {
  await noLinks(root); await noLinks(candidate); await noLinks(installedAsar);
  lockFile = path.join(root, ".office-entry-client-source.lock");
  await noLinks(lockFile, true);
  lock = await fs.open(lockFile, "wx", 0o600);
  await lock.writeFile(JSON.stringify({runId, pid: process.pid, purpose: "office-entry-r2-client-source"}));
  await verifySource(root, PINNED.formal);
  await verifySource(candidate, PINNED.candidate);
  assert.equal(sha(await bytes(installedAsar)), PINNED.installedAsarSha256);
  const installedPackage = JSON.parse(asar.extractFile(installedAsar, "package.json").toString("utf8"));
  assert.equal(installedPackage.version, "0.6.4");
  const installedMain = asar.extractFile(installedAsar, "electron/main.cjs");
  assert.equal(sha(installedMain), PINNED.installedR2MainSha256);
  assert.equal(PINNED.installedR2MainSha256, "a1d946169d81df58ab8bd21f75c566a8664633ff73faa9bbf37e6286df43b4d6");
  const installedRendererFiles = asar.listPackage(installedAsar)
    .map(name => name.replace(/^[/\\]/, "").replaceAll("\\", "/"))
    .filter(name => name.startsWith("dist/") && /\.(html|js|css|png|webp)$/.test(name)).sort();
  assert.equal(installedRendererFiles.length, 9, "Installed renderer inventory drifted");
  const installedRenderer = Object.fromEntries(installedRendererFiles.map(name => [name, sha(asar.extractFile(installedAsar, name.replaceAll('/', path.sep)))]));
  assert.equal(sha(await bytes(installedAsar)), PINNED.installedAsarSha256);
  const changes = [];
  for (const relative of Object.keys(PINNED.candidate).sort()) {
    const content = relative === "electron/main.cjs" ? installedMain : await bytes(within(candidate, relative), PINNED.candidate[relative]);
    const before = PINNED.formal[relative] ?? null, after = sha(content);
    if (before === after) continue;
    buffers.set(relative, content);
    changes.push({path: relative, existed: before !== null, beforeSha256: before, afterSha256: after,
      origin: relative === "electron/main.cjs" ? "installed-ASAR-original-R2-source" : "verified-0.6.4-client-source"});
  }
  assert.equal(changes.length, 21); assert.equal(changes.filter(item => item.existed).length, 18);
  assert.equal(changes.filter(item => !item.existed).length, 3);
  assert.deepEqual(await list(path.join(root, "dist")), Object.keys(PINNED.dist).sort());
  for (const [relative, hash] of Object.entries(PINNED.dist)) await bytes(within(path.join(root, "dist"), relative), hash);
  const desired = {...PINNED.candidate, "electron/main.cjs": PINNED.installedR2MainSha256};
  plan = {schema: 1, kind: "office-entry-installed-r2-client-source", runId, at: new Date().toISOString(),
    mode: apply ? "apply" : "dry-run", formalRoot: root, candidateRoot: candidate,
    installedAsar, installedAsarSha256: PINNED.installedAsarSha256, installedR2MainSha256: PINNED.installedR2MainSha256,
    installedRenderer,
    beforeVersion: "0.6.3", afterVersion: "0.6.4", selectedFiles: 97, unchangedFiles: 76,
    changes, beforeSource: PINNED.formal, desiredSource: desired, distBefore: PINNED.dist,
    backupSource: sourceBackup, backupDist: distBackup, serverTouched: false, hermesTouched: false,
    realProfileTouched: false, installedProgramModified: false, built: false, networkRequests: 0};
  await mkdirSafe(path.join(recordRoot, "evidence"));
  reportFile = path.join(recordRoot, "evidence", `source-recovery-${apply ? "apply" : "dry-run"}-${runId}.json`);
  if (!apply) {
    await writeNew(reportFile, Buffer.from(JSON.stringify({...plan, status: "DRY_RUN", sourceChanged: false}, null, 2)));
    console.log(JSON.stringify({status: "DRY_RUN", changes: 21, existing: 18, new: 3, evidence: reportFile, sourceChanged: false}));
  } else {
    assert(!await exists(sourceBackup) && !await exists(distBackup), "A prior source/dist backup exists; inspect it before retrying");
    await mkdirSafe(sourceBackup); await mkdirSafe(distBackup);
    // All before-images are safely stored before any authored source is changed.
    for (const item of changes.filter(item => item.existed))
      await writeNew(within(sourceBackup, item.path), await bytes(within(root, item.path), item.beforeSha256));
    for (const [relative, hash] of Object.entries(PINNED.dist))
      await writeNew(within(distBackup, relative), await bytes(within(path.join(root, "dist"), relative), hash));
    await writeNew(path.join(recordRoot, "backups/source-0.6.3-manifest.json"), Buffer.from(JSON.stringify(plan, null, 2)));
    await verifySource(root, PINNED.formal); await verifySource(candidate, PINNED.candidate);
    assert.equal(sha(await bytes(installedAsar)), PINNED.installedAsarSha256);
    for (const item of changes) {
      const destination = within(root, item.path); assert(allowedSource(item.path));
      await mkdirSafe(path.dirname(destination));
      touched.push(item);
      await replacePinned(destination, buffers.get(item.path), item.beforeSha256);
    }
    await verifySource(root, desired);
    assert.equal(JSON.parse(await bytes(path.join(root, "package.json"))).version, "0.6.4");
    for (const [relative, hash] of Object.entries(PINNED.dist)) await bytes(within(path.join(root, "dist"), relative), hash);
    assert.equal(sha(await bytes(installedAsar)), PINNED.installedAsarSha256);
    await writeNew(reportFile, Buffer.from(JSON.stringify({...plan, status: "PASS", sourceChanged: true, changedFiles: touched.length,
      mainMatchesInstalledR2: true, originalDistUnchanged: true, backupVerified: true}, null, 2)));
    console.log(JSON.stringify({status: "PASS", changedFiles: touched.length, version: "0.6.4", mainMatchesInstalledR2: true,
      backupSource: sourceBackup, backupDist: distBackup, evidence: reportFile, built: false}));
  }
} catch (error) {
  // Revert only our exact after-images; preserve any independently modified source.
  for (const item of touched.toReversed()) {
    try {
      const destination = within(root, item.path), actual = await currentHash(destination);
      if (actual === item.beforeSha256) {rollback.push({path: item.path, status: "ALREADY_ORIGINAL"}); continue;}
      assert.equal(actual, item.afterSha256, "Concurrent edit detected; refusing to overwrite");
      if (item.existed) await replacePinned(destination, await bytes(within(sourceBackup, item.path), item.beforeSha256), item.afterSha256);
      else await fs.unlink(destination); // Only a pinned new source leaf created by this invocation.
      rollback.push({path: item.path, status: "RESTORED"});
    } catch (restoreError) {rollback.push({path: item.path, status: "CONFLICT_RETAINED", message: restoreError.message});}
  }
  const failure = {at: new Date().toISOString(), runId, status: "FAIL", message: error.message, apply,
    sourceWritesAttempted: touched.length, rollback, built: false, installedProgramModified: false};
  try {
    await mkdirSafe(path.join(recordRoot, "evidence"));
    await writeNew(path.join(recordRoot, "evidence", `source-recovery-failure-${runId}.json`), Buffer.from(JSON.stringify(failure, null, 2)));
  } catch { /* Never weaken link or collision guards merely to save diagnostics. */ }
  console.error(JSON.stringify(failure)); process.exitCode = 1;
} finally {
  if (lock) {
    await lock.close();
    try {
      const record = JSON.parse(await bytes(lockFile));
      assert.equal(record.runId, runId); await fs.unlink(lockFile);
    } catch { console.error("Source lock changed or could not be removed; retained for review"); process.exitCode = 1; }
  }
}
